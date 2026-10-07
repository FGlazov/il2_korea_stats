"""Versions of the built-in templates and stylesheets/scripts (TD-25, FR-ADM-6).

Server owners override built-in files by copying them into `<data dir>/custom/`. When an il2ks upgrade changes such a
file, the copy silently keeps the old behaviour. To make that visible, every overridable built-in file carries a
version in its first line:

    {# il2ks-template: templates/il2ks/base.html v3.1 - copy this line along when you override #}    (templates)
    /* il2ks-template: static/il2ks/site.css v2.0 - copy this line along when you override */         (CSS, JS)

The version is two numbers, `vN.M`. **N** changes when the override contract changes: a block, an include, a context
variable, a custom tag or filter, an id or class that the built-in CSS or JS targets (`templatecontract.py` has the
exact rules). **M** is for everything else (wording, layout, colours, markup inside a block). An override on
an older N is `outdated` (red banner, installer message box, doctor warning); one that is only on an older M is
`behind` (shown in `il2ks custom list`). 0.1.0 shipped single numbers (`v3`): a header without `.M` reads as `vN.0`
everywhere.

The line travels with a copy, so an override says which version it is based on. `template_versions.json` (next to the
templates, in the `il2ks.web` package) records, per file, the version, the SHA-256 of its content *without* that line,
and its contract fingerprint (what a changed file is compared with to tell N from M: stored there rather than read from
git, so it works in a shallow clone, a sdist and an uncommitted branch). A test compares the registry with the files,
so a developer who changes a template cannot forget to raise its version: `il2ks dev bump-templates` does the
bookkeeping (adds missing headers, raises versions of changed files, rewrites the registry). This module is that
bookkeeping plus the header parsing the detection in `il2ks.serving.custom` needs.

Which files are versioned: HTML/TXT templates and CSS/JS static files of the `il2ks.web` package, except vendored
libraries (`vendor/`). Images and fonts are replaced as a whole, so they have no versions.
"""

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, cast

from il2ks.serving import templatecontract as contract_rules
from il2ks.serving.templatecontract import Contract

type Kind = Literal["templates", "static"]
KINDS: tuple[Kind, ...] = ("templates", "static")

REGISTRY_NAME = "template_versions.json"
REGISTRY_FORMAT = 2  # 1 (0.1.0): single-number versions, no fingerprints; still read
HEADER_TAG = "il2ks-template"
HEADER_NOTE = "copy this line along when you override"
HEADER_SCAN_LINES = 5  # an override may have a few lines of its own on top; the built-in files start with it

TEMPLATE_SUFFIXES = frozenset({".html", ".txt"})
STATIC_SUFFIXES = frozenset({".css", ".js"})
COMMENT_SUFFIXES = frozenset({".css", ".js"})  # `/* */`; everything else uses Django's `{# #}`
UNVERSIONED_DIRS = frozenset({"vendor"})

_VERSION = r"v(?P<major>\d+)(?:\.(?P<minor>\d+))?\b"
_DJANGO_HEADER = re.compile(r"^\s*\{#\s*" + HEADER_TAG + r":\s*(?P<key>\S+)\s+" + _VERSION + r".*?#\}\s*$")
_C_HEADER = re.compile(r"^\s*/\*\s*" + HEADER_TAG + r":\s*(?P<key>\S+)\s+" + _VERSION + r".*?\*/\s*$")

# Kept for `.github/workflows/release.yml`, which refuses a `v*` tag while it is False. It was flipped for 0.1.0; since
# then versions count up and there is no "before the first release" mode any more.
FIRST_RELEASE_DONE = True

type ActionKind = Literal["register", "add-header", "bump", "fix-header", "drop"]
type Part = Literal["major", "minor"]


@dataclass(frozen=True, slots=True, order=True)
class Version:
    """`vN.M`. Ordered (N first). Written `3.1`; a 0.1.0 header `v3` is `3.0`."""

    major: int
    minor: int = 0

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}"

    def next(self, part: Part) -> "Version":
        return Version(self.major + 1, 0) if part == "major" else Version(self.major, self.minor + 1)


def parse_version(text: str) -> Version | None:
    """`3`, `3.1`, `v3.1` -> Version; anything else (also `3.x`, an empty string) -> None."""
    found = re.fullmatch(r"v?(\d+)(?:\.(\d+))?", text.strip())
    return None if found is None else Version(int(found[1]), int(found[2] or 0))


@dataclass(frozen=True, slots=True)
class Header:
    key: str  # `templates/il2ks/base.html`, as written in the line
    version: Version
    line: int  # 0-based line number inside the file
    minor_given: bool = True  # False: an old `vN` header (0.1.0), read as `vN.0`


@dataclass(frozen=True, slots=True)
class Entry:
    version: Version
    sha256: str
    contract: Contract | None = None  # None: a format-1 registry, or a file never fingerprinted


@dataclass(frozen=True, slots=True)
class Action:
    """One thing `bump-templates` would do (or did)."""

    key: str
    kind: ActionKind
    old_version: Version | None
    new_version: Version | None
    message: str
    part: Part | None = None  # for "bump": which number was raised
    reasons: tuple[str, ...] = ()  # for a major bump: what changed in the contract


def web_root() -> Path:
    """The `il2ks/web` package folder: it holds `templates/`, `static/` and the registry."""
    return Path(__file__).resolve().parents[1] / "web"


def registry_path(root: Path | None = None) -> Path:
    return (root or web_root()) / REGISTRY_NAME


def is_versioned(kind: Kind, rel: str) -> bool:
    """Whether il2ks puts version lines into built-in files like this one (by kind, extension and folder)."""
    path = PurePosixPath(rel)
    suffixes = TEMPLATE_SUFFIXES if kind == "templates" else STATIC_SUFFIXES
    return path.suffix.lower() in suffixes and not UNVERSIONED_DIRS.intersection(path.parts)


def _pattern_for(rel: str) -> re.Pattern[str]:
    return _C_HEADER if PurePosixPath(rel).suffix.lower() in COMMENT_SUFFIXES else _DJANGO_HEADER


def header_line(key: str, version: Version) -> str:
    text = f"{HEADER_TAG}: {key} v{version} - {HEADER_NOTE}"
    return f"/* {text} */" if PurePosixPath(key).suffix.lower() in COMMENT_SUFFIXES else f"{{# {text} #}}"


def find_header(text: str, rel: str) -> Header | None:
    """The version line near the top of a file, or None. `rel` (the file's path) says which comment style to expect."""
    pattern = _pattern_for(rel)
    for number, line in enumerate(text.lstrip("﻿").splitlines()[:HEADER_SCAN_LINES]):
        found = pattern.match(line)
        if found:
            minor = found["minor"]
            return Header(found["key"], Version(int(found["major"]), int(minor or 0)), number, minor is not None)
    return None


def split_header(text: str, rel: str) -> tuple[Header | None, str]:
    """(header, the file content without the header line). Newlines are left as they are."""
    header = find_header(text, rel)
    if header is None:
        return None, text
    lines = text.lstrip("﻿").splitlines(keepends=True)
    del lines[header.line]
    return header, "".join(lines)


def content_hash(body: str) -> str:
    """SHA-256 of the content without its header line, line endings normalized (a Windows checkout may use CRLF)."""
    return hashlib.sha256(body.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def with_header(text: str, key: str, version: Version) -> str:
    """`text` with its version line set to `version`: replaced where it is, or added as the first line."""
    bom = "﻿" if text.startswith("﻿") else ""
    body = text.lstrip("﻿")
    newline = "\r\n" if "\r\n" in body else "\n"
    lines = body.splitlines(keepends=True)
    header = find_header(body, key)
    line = header_line(key, version)
    if header is None:
        return bom + line + newline + body
    ending = lines[header.line][len(lines[header.line].rstrip("\r\n")) :] or newline
    lines[header.line] = line + ending
    return bom + "".join(lines)


def version_of(path: Path) -> Version | None:
    """The version a file declares in its header (None: no header)."""
    header = find_header(path.read_text(encoding="utf-8", errors="replace"), path.name)
    return None if header is None else header.version


# --- the registry ---------------------------------------------------------------------------------------------------


def versioned_files(root: Path | None = None) -> dict[str, Path]:
    """Every versioned built-in file of the `il2ks.web` package: `templates/il2ks/base.html` -> its path."""
    base = root or web_root()
    found: dict[str, Path] = {}
    for kind in KINDS:
        folder = base / kind
        if not folder.is_dir():
            continue
        for path in sorted(p for p in folder.rglob("*") if p.is_file()):
            rel = path.relative_to(folder).as_posix()
            if is_versioned(kind, rel):
                found[f"{kind}/{rel}"] = path
    return found


def load_registry(root: Path | None = None) -> dict[str, Entry]:
    path = registry_path(root)
    return parse_registry(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _parse_contract(value: object) -> Contract | None:
    if not isinstance(value, dict):
        return None
    parsed: Contract = {}
    for field, names in cast(dict[object, object], value).items():
        if isinstance(field, str) and isinstance(names, list):
            parsed[field] = tuple(str(n) for n in cast(list[object], names))
    return parsed


def parse_registry(text: str) -> dict[str, Entry]:
    """Read registry JSON from text (also an old release's, from `git show <tag>:...`; format 1: integer versions)."""
    data: object = json.loads(text)
    files: object = cast(dict[str, object], data).get("files", {}) if isinstance(data, dict) else {}
    registry: dict[str, Entry] = {}
    if isinstance(files, dict):
        for key, value in cast(dict[object, object], files).items():
            if isinstance(key, str) and isinstance(value, dict):
                entry = cast(dict[str, object], value)
                raw, digest = entry.get("version"), entry.get("sha256")
                version = Version(raw) if isinstance(raw, int) else parse_version(raw) if isinstance(raw, str) else None
                if version is not None and isinstance(digest, str):
                    registry[key] = Entry(version, digest, _parse_contract(entry.get("contract")))
    return registry


def save_registry(registry: dict[str, Entry], root: Path | None = None) -> None:
    files: dict[str, dict[str, object]] = {}
    for key, e in sorted(registry.items()):
        item: dict[str, object] = {"version": str(e.version), "sha256": e.sha256}
        if e.contract is not None:
            item["contract"] = {field: list(names) for field, names in sorted(e.contract.items())}
        files[key] = item
    document = {"format": REGISTRY_FORMAT, "files": files}
    registry_path(root).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


@dataclass(frozen=True, slots=True)
class _Plan:
    action: Action | None
    new_text: str | None  # the file after the action, None when it stays as it is
    entry: Entry


@dataclass(frozen=True, slots=True)
class _Read:
    key: str
    path: Path
    text: str
    header: Header | None
    digest: str
    contract: Contract


def _read(key: str, path: Path) -> _Read:
    text = path.read_bytes().decode("utf-8")  # not read_text: that would turn CRLF into LF when written back
    header, body = split_header(text, key)
    return _Read(key, path, text, header, content_hash(body), contract_rules.fingerprint(key, body))


def _forced(key: str, major: Iterable[str] | None) -> bool:
    """`--major` names: all changed files when the list is empty, else only those (full key, or the path inside
    `templates/` / `static/`)."""
    if major is None:
        return False
    names = [m.replace("\\", "/") for m in major]
    return not names or any(key == n or key.partition("/")[2] == n for n in names)


def _decide_part(
    read: _Read, entry: Entry, targeted: frozenset[str], *, forced: bool
) -> tuple[Part, tuple[str, ...], str]:
    if forced:
        return "major", (), "forced with --major"
    if entry.contract is None:
        return "major", (), "no stored fingerprint to compare with, so treated as breaking (use --major to be explicit)"
    reasons = tuple(contract_rules.breaking_changes(read.key, entry.contract, read.contract, targeted))
    if reasons:
        return "major", reasons, "the override contract changed"
    return "minor", (), "the content changed, the override contract did not"


def _plan_file(read: _Read, entry: Entry | None, targeted: frozenset[str], *, forced: bool) -> _Plan:
    key, header = read.key, read.header
    if entry is None:
        version = header.version if header is not None else Version(1, 0)
        kind: ActionKind = "register" if header is not None else "add-header"
        why = "new file, not in the registry" if header is not None else "new file without a version line"
        action = Action(key, kind, None, version, why)
        needs_text = header is None or not header.minor_given
        text = with_header(read.text, key, version) if needs_text else None
        return _Plan(action, text, Entry(version, read.digest, read.contract))
    if header is None or read.digest != entry.sha256:
        old_header_bump = (
            header is not None
            and not header.minor_given
            and header.version.major > entry.version.major
            and entry.contract is not None
        )  # an old (single-number) tool bumped it on a branch: judge the change again
        if header is not None and header.version > entry.version and not old_header_bump:
            # the developer raised the number by hand: believe it
            text = with_header(read.text, key, header.version) if not header.minor_given else None
            action = Action(
                key, "register", entry.version, header.version, "version raised by hand, registry is behind"
            )
            return _Plan(action, text, Entry(header.version, read.digest, read.contract))
        if read.digest == entry.sha256:  # only the line is missing
            version = entry.version
            action = Action(key, "add-header", None, version, "the version line is missing")
            return _Plan(action, with_header(read.text, key, version), Entry(version, read.digest, read.contract))
        part, reasons, why = _decide_part(read, entry, targeted, forced=forced)
        version = entry.version.next(part)
        action = Action(key, "bump", entry.version, version, why, part, reasons)
        return _Plan(action, with_header(read.text, key, version), Entry(version, read.digest, read.contract))
    # the content is as registered
    if header.version == entry.version and header.minor_given:
        if entry.contract == read.contract:
            return _Plan(None, None, entry)
        return _Plan(
            Action(key, "register", entry.version, entry.version, "contract fingerprint recorded"),
            None,
            Entry(entry.version, read.digest, read.contract),
        )
    why = f"the version line says v{header.version}, the registry says v{entry.version}"
    if header.version == entry.version:
        n = header.version.major
        why = f"the version line has no minor number yet (0.1.0 style): v{n} is written v{n}.0"
    action = Action(key, "fix-header", header.version, entry.version, why)
    return _Plan(action, with_header(read.text, key, entry.version), Entry(entry.version, read.digest, read.contract))


def bump_templates(root: Path | None = None, *, write: bool = True, major: Iterable[str] | None = None) -> list[Action]:
    """Bring headers and registry in line with the files: add missing version lines (v1.0), raise the version of changed
    files (N when the override contract changed or `major` forces it, else M), add new files to the registry, drop
    vanished ones. Unchanged files are left alone. `major`: None = infer everywhere; an iterable = force N for the
    changed files it names (empty: for every changed file). Returns what was (or, with `write=False`, would be) done;
    an empty list means everything is in order."""
    registry = load_registry(root)
    updated = dict(registry)
    actions: list[Action] = []
    files = versioned_files(root)
    reads = [_read(key, path) for key, path in files.items()]
    targeted = contract_rules.targeted_names(
        {r.key: r.contract for r in reads}, {k: e.contract for k, e in registry.items() if e.contract is not None}
    )
    for read in reads:
        plan = _plan_file(read, registry.get(read.key), targeted, forced=_forced(read.key, major))
        updated[read.key] = plan.entry
        if plan.action is None:
            continue
        actions.append(plan.action)
        if write and plan.new_text is not None:
            read.path.write_bytes(plan.new_text.encode("utf-8"))
    for key, entry in registry.items():
        if key not in files:
            del updated[key]
            actions.append(Action(key, "drop", entry.version, None, "the file no longer exists"))
    if write and actions:
        save_registry(updated, root)
    return actions


def describe(action: Action) -> str:
    versions = {
        "register": f"register v{action.new_version}",
        "add-header": f"add version line v{action.new_version}",
        "bump": f"v{action.old_version} -> v{action.new_version} ({'BREAKING, N' if action.part == 'major' else 'M'})",
        "fix-header": f"set version line to v{action.new_version}",
        "drop": "remove from the registry",
    }[action.kind]
    text = f"{action.key}: {versions} ({action.message})"
    return text + "".join(f"\n      - {reason}" for reason in action.reasons)


# --- release notes --------------------------------------------------------------------------------------------------


def registry_changes(old: dict[str, Entry], new: dict[str, Entry], *, everything: bool = False) -> list[str]:
    """Release-note lines. Only what overrides must look at: files whose N went up, and removed files. `everything`
    adds the M-only bumps and new files."""
    lines: list[str] = []
    for key in sorted(old.keys() | new.keys()):
        before, after = old.get(key), new.get(key)
        if before is None and after is not None:
            if everything:
                lines.append(f"{key}: new (v{after.version})")
        elif before is not None and after is None:
            lines.append(f"{key}: removed")
        elif before is not None and after is not None and before.version != after.version:
            if after.version.major != before.version.major:
                lines.append(f"{key}: v{before.version} -> v{after.version}")
            elif everything:
                lines.append(f"{key}: v{before.version} -> v{after.version} (minor)")
    return lines
