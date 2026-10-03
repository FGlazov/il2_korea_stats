"""`custom/` overrides (TD-25, FR-ADM-6): copy a built-in template or static file into `<data dir>/custom/`, and notice
when an upgrade changed the page an override was based on.

Overriding is just "a file with the same path in custom/ wins" (see `settings.py`); this module adds the paperwork.

How an override is judged (`scan`): built-in templates, stylesheets and scripts carry a version line in their first
line (`il2ks.serving.templateversions`), and it travels with a copy. Comparing the version in the override with the one
in the built-in file gives the state: `current`, `outdated`, `newer`, `unversioned` (no version line), `orphan` (the
built-in file is gone), `custom-only` (a new file that replaces nothing), `unchecked` (the built-in file has no
version, like a vendored library). `custom/.il2ks-overrides.json` is extra evidence: `il2ks custom copy` records the
SHA-256 of the original and its version, which tells a copy whose version line was deleted by hand ("copied from v2,
then edited") from one nobody knows anything about.

Built-in files are found without Django running: the app folders of `INSTALLED_APPS`, in that order (Django's own
order), each with a `templates/` and a `static/` folder.
"""

import difflib
import functools
import hashlib
import importlib.util
import json
import logging
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal, cast

from il2ks import __version__
from il2ks.config import Config
from il2ks.serving import templateversions
from il2ks.serving.djsettings import INSTALLED_APPS
from il2ks.serving.templateversions import KINDS, Kind

__all__ = ["KINDS", "Kind"]

OVERRIDES_FILE = ".il2ks-overrides.json"
RECORD_FORMAT = 1

type State = Literal["current", "outdated", "newer", "unversioned", "orphan", "custom-only", "unchecked"]
PROBLEM_STATES: frozenset[State] = frozenset({"outdated", "newer", "unversioned", "orphan"})


class CustomError(ValueError):
    """The request can't be done; the message tells the admin why and what to try."""


@dataclass(frozen=True, slots=True)
class OverrideCheck:
    """One file in `custom/templates` or `custom/static` and how it relates to the built-in file of that name."""

    kind: Kind
    rel: str  # path inside templates/ or static/, with forward slashes
    state: State
    override: Path
    original: Path | None  # the built-in file now, None if there is none
    override_version: int | None  # from the version line (or, failing that, from the copy record)
    builtin_version: int | None
    message: str  # what is the matter, one plain sentence (also for the good states)
    fix: str  # what to do about it; '' when nothing

    @property
    def key(self) -> str:
        return f"{self.kind}/{self.rel}"

    @property
    def is_problem(self) -> bool:
        return self.state in PROBLEM_STATES


@dataclass(frozen=True, slots=True)
class Placed:
    kind: Kind
    rel: str
    override: Path
    original: Path


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


def load_records(cfg: Config) -> dict[str, dict[str, str]]:
    return _load_records_at(overrides_path(cfg))


def _load_records_at(path: Path) -> dict[str, dict[str, str]]:
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
    record = {
        "original_sha256": sha256_of(original),
        "il2ks_version": __version__,
        "copied": datetime.now(UTC).date().isoformat(),
    }
    version = templateversions.version_of(original)
    if version is not None:
        record["template_version"] = str(version)
    return record


def copy_builtin(cfg: Config, spec: str, *, force: bool = False) -> Placed:
    """Copy a built-in file into `custom/` (its version line comes along) and record its hash. Won't replace an
    existing override unless `force`."""
    kind, rel, original = resolve(spec)
    target = custom_dir(cfg) / kind / rel
    if target.exists() and not force:
        raise CustomError(f"{target} already exists (your override). Use --force to replace it with a fresh copy")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(original, target)
    records = load_records(cfg)
    records[f"{kind}/{rel}"] = _record_for(original)
    _save_records(cfg, records)
    return Placed(kind, rel, target, original)


def accept_original(cfg: Config, spec: str) -> Placed:
    """The admin has looked at the changed original and brought the override up to date: the override's version line
    is set to the built-in file's version (added when missing) and the copy record is refreshed."""
    kind, rel, original = resolve(spec)
    target = custom_dir(cfg) / kind / rel
    if not target.is_file():
        raise CustomError(f"you have no override of {kind}/{rel}. Use `il2ks custom copy {kind}/{rel}` first")
    version = templateversions.version_of(original)
    if version is not None:
        text = target.read_bytes().decode("utf-8")  # not read_text: it would turn CRLF into LF
        target.write_bytes(templateversions.with_header(text, f"{kind}/{rel}", version).encode("utf-8"))
    records = load_records(cfg)
    records[f"{kind}/{rel}"] = _record_for(original)
    _save_records(cfg, records)
    return Placed(kind, rel, target, original)


# --- judging overrides ----------------------------------------------------------------------------------------------


def _fix_update(key: str) -> str:
    return (
        f"Compare your file with the new built-in one (`il2ks custom diff {key}`), bring over what you need, "
        f"then run `il2ks custom accept {key}`."
    )


def _judge(kind: Kind, rel: str, override: Path, record: dict[str, str] | None) -> OverrideCheck:
    key = f"{kind}/{rel}"
    original = builtin_file(kind, rel)
    versioned = templateversions.is_versioned(kind, rel)
    text = override.read_text(encoding="utf-8", errors="replace") if versioned else ""  # images etc. have no header
    header = templateversions.find_header(text, rel)
    declared = header.version if header is not None else None

    def result(
        state: State, message: str, fix: str = "", *, mine: int | None = declared, built: int | None = None
    ) -> OverrideCheck:
        return OverrideCheck(kind, rel, state, override, original, mine, built, message, fix)

    if original is None:
        if header is None and record is None:
            return result("custom-only", "A file of your own: it replaces nothing, so an upgrade cannot break it.")
        return result(
            "orphan",
            "il2ks no longer has a built-in file with this name, so this one is probably not used any more.",
            "Delete it, or keep it if that is deliberate (for example a page of your own).",
        )
    built = templateversions.version_of(original) if versioned else None
    recorded_hash = record.get("original_sha256") if record else None
    copy_note = f" (copied with il2ks {record['il2ks_version']})" if record and "il2ks_version" in record else ""
    if built is None:
        # Nothing to compare versions with: a vendored library, Django's own admin file, or a built-in file that has no
        # version line yet. The copy record is all there is.
        if recorded_hash is None:
            return result("unchecked", "The built-in file has no version, so il2ks can't tell if it changed.")
        if recorded_hash == sha256_of(original):
            return result("current", "Matches the built-in file you copied it from.")
        return result(
            "outdated",
            f"The built-in file changed since you copied it{copy_note}.",
            _fix_update(key),
        )
    if declared is None:
        if recorded_hash == sha256_of(original):
            return result("current", "No version line, but it was copied from the current built-in file.", built=built)
        recorded_version = (
            int(record["template_version"]) if record and record.get("template_version", "").isdigit() else None
        )
        if recorded_version is not None:
            return result(
                "outdated",
                f"Based on version {recorded_version} (from your copy record){copy_note}, but this il2ks has version "
                f"{built}: the built-in file changed.",
                _fix_update(key),
                mine=recorded_version,
                built=built,
            )
        return result(
            "unversioned",
            f"It has no il2ks-template version line, so il2ks can't tell which version it is based on{copy_note}.",
            _fix_update(key),
            built=built,
        )
    if declared == built:
        return result("current", f"Based on the current version ({built}).", built=built)
    if declared < built:
        return result(
            "outdated",
            f"Based on version {declared}, but this il2ks has version {built}: the built-in file changed.",
            _fix_update(key),
            built=built,
        )
    return result(
        "newer",
        f"Based on version {declared}, but this il2ks only has version {built} (was il2ks downgraded?).",
        _fix_update(key),
        built=built,
    )


def scan(custom_root: Path, records: dict[str, dict[str, str]] | None = None) -> list[OverrideCheck]:
    """Every file in `custom_root/templates` and `custom_root/static`, judged. `records` are the copy records
    (default: the ones next to it; a damaged record file counts as no records, `il2ks doctor` reports it)."""
    if records is None:
        try:
            records = _load_records_at(custom_root / OVERRIDES_FILE)
        except CustomError:
            records = {}
    checks: list[OverrideCheck] = []
    for kind in KINDS:
        base = custom_root / kind
        if not base.is_dir():
            continue
        for path in sorted(p for p in base.rglob("*") if p.is_file() and not p.name.startswith(".")):
            rel = path.relative_to(base).as_posix()
            checks.append(_judge(kind, rel, path, records.get(f"{kind}/{rel}")))
    return checks


def override_checks(cfg: Config) -> list[OverrideCheck]:
    return scan(custom_dir(cfg))


@functools.cache
def startup_scan(custom_root: Path) -> tuple[OverrideCheck, ...]:
    """`scan`, computed once per process: overrides only take effect after a restart, so the answer cannot change while
    the site runs (the admin banner asks on every admin page). Tests clear it with `startup_scan.cache_clear()`."""
    return tuple(scan(custom_root))


def startup_problems(custom_root: Path) -> list[OverrideCheck]:
    return [c for c in startup_scan(custom_root) if c.is_problem]


def builtin_listing(kind: Kind) -> list[str]:
    """Every built-in file of this kind (relative paths), for `il2ks custom list --builtin`."""
    names: set[str] = set()
    for root in builtin_roots(kind):
        names.update(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    return sorted(names)


def diff_override(cfg: Config, spec: str) -> str:
    """A unified diff from the override to the current built-in file, with a short explanation on top. `-` lines are
    yours, `+` lines are in the built-in file (new things from the upgrade, or things you changed)."""
    kind, rel, original = resolve(spec)
    override = custom_dir(cfg) / kind / rel
    if not override.is_file():
        raise CustomError(
            f"you have no override of {kind}/{rel} (nothing at {override}). `il2ks custom copy` makes one"
        )
    checks = [c for c in override_checks(cfg) if c.key == f"{kind}/{rel}"]
    mine = override.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    theirs = original.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    lines = [f"{kind}/{rel}: {checks[0].message}" if checks else f"{kind}/{rel}"]
    lines.append(
        "il2ks does not keep old versions of its files, so this compares your file with the built-in one as it is now: "
        "the differences are the upgrade's changes plus your own edits."
    )
    body = list(difflib.unified_diff(mine, theirs, "yours (custom)", "built-in (now)"))
    if not body:
        lines.append("The two files are identical.")
    else:
        lines.append("`-` = only in your file, `+` = only in the built-in file.\n")
        lines.extend(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in body)
    if checks and checks[0].is_problem and checks[0].fix:
        lines.append(f"\nWhen your file is up to date: `il2ks custom accept {kind}/{rel}` (sets its version line).")
    return "".join(line if line.endswith("\n") else line + "\n" for line in lines)


def log_problems(custom_root: Path, logger: logging.Logger) -> list[OverrideCheck]:
    """Write one WARNING per override that needs attention (at `il2ks web` / `il2ks run` start). Returns them."""
    problems = startup_problems(custom_root)
    if problems:
        logger.warning(
            "%d file(s) in %s are based on an older il2ks or an unknown one and may break or hide new content. "
            "`il2ks custom list` shows them; `il2ks custom diff <path>` shows what differs.",
            len(problems),
            custom_root,
        )
    for item in problems:
        logger.warning("custom override %s: %s %s", item.key, item.message, item.fix)
    return problems
