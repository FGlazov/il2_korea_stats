"""`custom/` overrides (TD-25, FR-ADM-6): copy a built-in template or static file into `<data dir>/custom/`, remember
what the original looked like, and notice when an upgrade changes the original.

Overriding is just "a file with the same path in custom/ wins" (see `settings.py`); this module only adds the paperwork.
`custom/.il2ks-overrides.json` records, for every file copied with `il2ks custom copy`, the SHA-256 of the original at
that moment. Later, `il2ks custom list` and `il2ks doctor` compare it with the original that ships now: a different hash
means the new version of il2ks changed the page the override was based on, so the override may now be out of date.

Built-in files are found without Django running: the app folders of `INSTALLED_APPS`, in that order (Django's own
order), each with a `templates/` and a `static/` folder.
"""

import hashlib
import importlib.util
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal, cast

from il2ks import __version__
from il2ks.config import Config
from il2ks.serving.djsettings import INSTALLED_APPS

type Kind = Literal["templates", "static"]
KINDS: tuple[Kind, ...] = ("templates", "static")
OVERRIDES_FILE = ".il2ks-overrides.json"
RECORD_FORMAT = 1

type State = Literal["ok", "original-changed", "original-missing", "override-deleted"]


class CustomError(ValueError):
    """The request can't be done; the message tells the admin why and what to try."""


@dataclass(frozen=True, slots=True)
class OverrideStatus:
    kind: Kind
    rel: str  # path inside templates/ or static/, with forward slashes
    state: State
    override: Path
    original: Path | None  # the built-in file now, None if there is none
    copied_version: str  # il2ks version at copy time

    @property
    def key(self) -> str:
        return f"{self.kind}/{self.rel}"


def custom_dir(cfg: Config) -> Path:
    return cfg.data_dir / "custom"


def overrides_path(cfg: Config) -> Path:
    return custom_dir(cfg) / OVERRIDES_FILE


def builtin_roots(kind: Kind) -> list[Path]:
    """The folders that hold built-in templates (or static files), in lookup order."""
    roots: list[Path] = []
    for module in INSTALLED_APPS:
        spec = importlib.util.find_spec(module)
        if spec is None or spec.submodule_search_locations is None:
            continue
        for location in spec.submodule_search_locations:
            root = Path(location) / kind
            if root.is_dir():
                roots.append(root)
    return roots


def builtin_file(kind: Kind, rel: str) -> Path | None:
    for root in builtin_roots(kind):
        candidate = root / rel
        if candidate.is_file():
            return candidate
    return None


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def as_kind(text: str) -> Kind | None:
    return "templates" if text == "templates" else "static" if text == "static" else None


def _clean_rel(text: str) -> str:
    rel = PurePosixPath(text.strip().replace("\\", "/"))
    if rel.is_absolute() or ".." in rel.parts or not rel.parts or str(rel) == ".":
        raise CustomError(f"{text!r} is not a path inside the built-in templates or static files")
    return str(rel)


def resolve(spec: str) -> tuple[Kind, str, Path]:
    """Find the built-in file `spec` names: `templates/il2ks/home.html`, `static/css/site.css`, or the same without
    the leading `templates/` / `static/` when only one of them has such a file."""
    rel = _clean_rel(spec)
    first, _, rest = rel.partition("/")
    kind = as_kind(first)
    if kind is not None and rest:
        found = builtin_file(kind, rest)
        if found is not None:
            return kind, rest, found
    matches: list[tuple[Kind, str, Path]] = []
    for k in KINDS:
        found = builtin_file(k, rel)
        if found is not None:
            matches.append((k, rel, found))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise CustomError(f"{rel} exists as a template and as a static file: write templates/{rel} or static/{rel}")
    raise CustomError(f"no built-in template or static file {rel!r}. `il2ks custom list --builtin` shows what exists")


def _load_records(cfg: Config) -> dict[str, dict[str, str]]:
    path = overrides_path(cfg)
    if not path.is_file():
        return {}
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CustomError(f"{path} is damaged ({exc}); delete it to start the list over") from exc
    if not isinstance(data, dict):
        raise CustomError(f"{path} is damaged; delete it to start the list over")
    overrides: object = cast(dict[str, object], data).get("overrides", {})
    if not isinstance(overrides, dict):
        raise CustomError(f"{path} is damaged; delete it to start the list over")
    records: dict[str, dict[str, str]] = {}
    for key, value in cast(dict[object, object], overrides).items():
        if isinstance(key, str) and isinstance(value, dict):
            records[key] = {str(k): str(v) for k, v in cast(dict[object, object], value).items()}
    return records


def _save_records(cfg: Config, records: dict[str, dict[str, str]]) -> None:
    path = overrides_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"format": RECORD_FORMAT, "overrides": dict(sorted(records.items()))}
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _record_for(original: Path) -> dict[str, str]:
    return {
        "original_sha256": sha256_of(original),
        "il2ks_version": __version__,
        "copied": datetime.now(UTC).date().isoformat(),
    }


def copy_builtin(cfg: Config, spec: str, *, force: bool = False) -> OverrideStatus:
    """Copy a built-in file into `custom/` and record its hash. Won't replace an existing override unless `force`."""
    kind, rel, original = resolve(spec)
    target = custom_dir(cfg) / kind / rel
    if target.exists() and not force:
        raise CustomError(f"{target} already exists (your override). Use --force to replace it with a fresh copy")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(original, target)
    records = _load_records(cfg)
    record = _record_for(original)
    records[f"{kind}/{rel}"] = record
    _save_records(cfg, records)
    return OverrideStatus(kind, rel, "ok", target, original, record["il2ks_version"])


def accept_original(cfg: Config, spec: str) -> OverrideStatus:
    """The admin has looked at the changed original and brought the override up to date: record the new hash."""
    kind, rel, original = resolve(spec)
    records = _load_records(cfg)
    key = f"{kind}/{rel}"
    target = custom_dir(cfg) / kind / rel
    if key not in records or not target.is_file():
        raise CustomError(f"{key} is not a recorded override. Use `il2ks custom copy {key}` first")
    records[key] = _record_for(original)
    _save_records(cfg, records)
    return OverrideStatus(kind, rel, "ok", target, original, records[key]["il2ks_version"])


def override_statuses(cfg: Config) -> list[OverrideStatus]:
    """Every recorded override and whether its original still matches what it was copied from."""
    result: list[OverrideStatus] = []
    for key, record in _load_records(cfg).items():
        kind_text, _, rel = key.partition("/")
        kind = as_kind(kind_text)
        if kind is None or not rel:
            continue
        override = custom_dir(cfg) / kind / rel
        original = builtin_file(kind, rel)
        state: State
        if not override.is_file():
            state = "override-deleted"
        elif original is None:
            state = "original-missing"
        elif sha256_of(original) != record.get("original_sha256"):
            state = "original-changed"
        else:
            state = "ok"
        result.append(OverrideStatus(kind, rel, state, override, original, record.get("il2ks_version", "?")))
    return result


def untracked_overrides(cfg: Config) -> list[tuple[Kind, str, Path]]:
    """Files in `custom/` that replace a built-in file but were never recorded (put there by hand): nothing can tell
    whether the original has changed since."""
    recorded = {s.key for s in override_statuses(cfg)}
    found: list[tuple[Kind, str, Path]] = []
    for kind in KINDS:
        base = custom_dir(cfg) / kind
        if not base.is_dir():
            continue
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            rel = path.relative_to(base).as_posix()
            original = builtin_file(kind, rel)
            if original is not None and f"{kind}/{rel}" not in recorded:
                found.append((kind, rel, original))
    return found


def builtin_listing(kind: Kind) -> list[str]:
    """Every built-in file of this kind (relative paths), for `il2ks custom list --builtin`."""
    names: set[str] = set()
    for root in builtin_roots(kind):
        names.update(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    return sorted(names)
