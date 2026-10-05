"""Versions of the built-in templates and stylesheets/scripts (TD-25, FR-ADM-6).

Server owners override built-in files by copying them into `<data dir>/custom/`. When an il2ks upgrade changes such a
file, the copy silently keeps the old behaviour. To make that visible, every overridable built-in file carries a
version in its first line:

    {# il2ks-template: templates/il2ks/base.html v3 - copy this line along when you override #}      (templates)
    /* il2ks-template: static/il2ks/site.css v2 - copy this line along when you override */           (CSS, JS)

The line travels with a copy, so an override says which version it is based on. `template_versions.json` (next to the
templates, in the `il2ks.web` package) records, per file, the version and the SHA-256 of its content *without* that
line. A test compares it with the files, so a developer who changes a template cannot forget to raise its version:
`il2ks dev bump-templates` does the bookkeeping (adds missing headers, raises versions of changed files, rewrites the
registry). This module is that bookkeeping plus the header parsing the detection in `il2ks.serving.custom` needs.

Before the first public release nobody can have an override based on an older version, so the numbers would only be
noise: while `FIRST_RELEASE_DONE` is False every file stays at v1 and `bump-templates` only refreshes the content
hashes in the registry. The constant was flipped for the first release (0.1.0); since then versions count up.

Which files are versioned: HTML/TXT templates and CSS/JS static files of the `il2ks.web` package, except vendored
libraries (`vendor/`). Images and fonts are replaced as a whole, so they have no versions.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, cast

type Kind = Literal["templates", "static"]
KINDS: tuple[Kind, ...] = ("templates", "static")

REGISTRY_NAME = "template_versions.json"
REGISTRY_FORMAT = 1
HEADER_TAG = "il2ks-template"
HEADER_NOTE = "copy this line along when you override"
HEADER_SCAN_LINES = 5  # an override may have a few lines of its own on top; the built-in files start with it

TEMPLATE_SUFFIXES = frozenset({".html", ".txt"})
STATIC_SUFFIXES = frozenset({".css", ".js"})
COMMENT_SUFFIXES = frozenset({".css", ".js"})  # `/* */`; everything else uses Django's `{# #}`
UNVERSIONED_DIRS = frozenset({"vendor"})

_DJANGO_HEADER = re.compile(r"^\s*\{#\s*" + HEADER_TAG + r":\s*(?P<key>\S+)\s+v(?P<version>\d+)\b.*?#\}\s*$")
_C_HEADER = re.compile(r"^\s*/\*\s*" + HEADER_TAG + r":\s*(?P<key>\S+)\s+v(?P<version>\d+)\b.*?\*/\s*$")

# True since the first public release (0.1.0, docs/releasing.md); before it every built-in file stayed at version 1
# and a changed file only got a new hash in the registry. An explicit constant rather than something
# derived (a git tag, `il2ks.__version__`): tags are missing in shallow clones and sdists, `__version__` also moves for
# pre-releases, and the switch should be a visible, reviewed change in the release commit.
FIRST_RELEASE_DONE = True

type ActionKind = Literal["register", "add-header", "bump", "fix-header", "rehash", "drop"]


@dataclass(frozen=True, slots=True)
class Header:
    key: str  # `templates/il2ks/base.html`, as written in the line
    version: int
    line: int  # 0-based line number inside the file


@dataclass(frozen=True, slots=True)
class Entry:
    version: int
    sha256: str


@dataclass(frozen=True, slots=True)
class Action:
    """One thing `bump-templates` would do (or did)."""

    key: str
    kind: ActionKind
    old_version: int | None
    new_version: int | None
    message: str


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


def header_line(key: str, version: int) -> str:
    text = f"{HEADER_TAG}: {key} v{version} - {HEADER_NOTE}"
    return f"/* {text} */" if PurePosixPath(key).suffix.lower() in COMMENT_SUFFIXES else f"{{# {text} #}}"


def find_header(text: str, rel: str) -> Header | None:
    """The version line near the top of a file, or None. `rel` (the file's path) says which comment style to expect."""
    pattern = _pattern_for(rel)
    for number, line in enumerate(text.lstrip("﻿").splitlines()[:HEADER_SCAN_LINES]):
        found = pattern.match(line)
        if found:
            return Header(found["key"], int(found["version"]), number)
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


def with_header(text: str, key: str, version: int) -> str:
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


def version_of(path: Path) -> int | None:
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


def parse_registry(text: str) -> dict[str, Entry]:
    """Read registry JSON from text (also an old release's, from `git show <tag>:...`)."""
    data: object = json.loads(text)
    files: object = cast(dict[str, object], data).get("files", {}) if isinstance(data, dict) else {}
    registry: dict[str, Entry] = {}
    if isinstance(files, dict):
        for key, value in cast(dict[object, object], files).items():
            if isinstance(key, str) and isinstance(value, dict):
                entry = cast(dict[str, object], value)
                version, digest = entry.get("version"), entry.get("sha256")
                if isinstance(version, int) and isinstance(digest, str):
                    registry[key] = Entry(version, digest)
    return registry


def save_registry(registry: dict[str, Entry], root: Path | None = None) -> None:
    files = {key: {"version": e.version, "sha256": e.sha256} for key, e in sorted(registry.items())}
    document = {"format": REGISTRY_FORMAT, "files": files}
    registry_path(root).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


@dataclass(frozen=True, slots=True)
class _Plan:
    action: Action | None
    new_text: str | None  # the file after the action, None when it stays as it is
    entry: Entry


def _plan_prerelease(key: str, text: str, entry: Entry | None, header: Header | None, digest: str) -> _Plan:
    """Before the first release: the version is 1 for every file; only the hash follows the content."""
    new_entry = Entry(1, digest)
    new_text = None if header is not None and header.version == 1 else with_header(text, key, 1)
    if entry is None:
        kind: ActionKind = "register" if header is not None else "add-header"
        return _Plan(Action(key, kind, None, 1, "new file, not in the registry"), new_text, new_entry)
    if header is None:
        return _Plan(Action(key, "add-header", None, 1, "the version line is missing"), new_text, new_entry)
    if header.version != 1:
        why = f"the version line says v{header.version}; versions stay at v1 until the first release"
        return _Plan(Action(key, "fix-header", header.version, 1, why), new_text, new_entry)
    if entry.version != 1 or entry.sha256 != digest:
        why = "the content changed (versions stay at v1 before the first release)"
        return _Plan(Action(key, "rehash", 1, 1, why), None, new_entry)
    return _Plan(None, None, entry)


def _plan_file(key: str, text: str, entry: Entry | None) -> _Plan:
    header, body = split_header(text, key)
    digest = content_hash(body)
    if not FIRST_RELEASE_DONE:
        return _plan_prerelease(key, text, entry, header, digest)
    if entry is None:
        version = header.version if header is not None else 1
        kind: ActionKind = "register" if header is not None else "add-header"
        why = "new file, not in the registry" if header is not None else "new file without a version line"
        action = Action(key, kind, None, version, why)
        return _Plan(action, None if header is not None else with_header(text, key, version), Entry(version, digest))
    if header is None:
        version = entry.version if digest == entry.sha256 else entry.version + 1
        action = Action(key, "add-header", None, version, "the version line is missing")
        return _Plan(action, with_header(text, key, version), Entry(version, digest))
    if digest == entry.sha256:
        if header.version == entry.version:
            return _Plan(None, None, entry)
        why = f"the version line says v{header.version}, the registry says v{entry.version}"
        action = Action(key, "fix-header", header.version, entry.version, why)
        return _Plan(action, with_header(text, key, entry.version), entry)
    if header.version > entry.version:  # the developer raised the number by hand: believe it
        action = Action(key, "register", entry.version, header.version, "version raised by hand, registry is behind")
        return _Plan(action, None, Entry(header.version, digest))
    version = entry.version + 1
    action = Action(key, "bump", entry.version, version, "the content changed")
    return _Plan(action, with_header(text, key, version), Entry(version, digest))


def bump_templates(root: Path | None = None, *, write: bool = True) -> list[Action]:
    """Bring headers and registry in line with the files: add missing version lines (v1), raise the version of changed
    files (before the first release: keep v1, only refresh the hash), add new files to the registry, drop vanished
    ones. Unchanged files are left alone. Returns what was (or, with `write=False`, would be) done; an empty list
    means everything is in order."""
    registry = load_registry(root)
    updated = dict(registry)
    actions: list[Action] = []
    files = versioned_files(root)
    for key, path in files.items():
        text = path.read_bytes().decode("utf-8")  # not read_text: that would turn CRLF into LF when written back
        plan = _plan_file(key, text, registry.get(key))
        updated[key] = plan.entry
        if plan.action is None:
            continue
        actions.append(plan.action)
        if write and plan.new_text is not None:
            path.write_bytes(plan.new_text.encode("utf-8"))
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
        "bump": f"v{action.old_version} -> v{action.new_version}",
        "fix-header": f"set version line to v{action.new_version}",
        "rehash": f"update the registry hash, stays v{action.new_version}",
        "drop": "remove from the registry",
    }[action.kind]
    return f"{action.key}: {versions} ({action.message})"


# --- release notes --------------------------------------------------------------------------------------------------


def registry_changes(old: dict[str, Entry], new: dict[str, Entry]) -> list[str]:
    """Release-note lines: files whose version went up, files added, files removed (ignores unchanged ones)."""
    lines: list[str] = []
    for key in sorted(old.keys() | new.keys()):
        before, after = old.get(key), new.get(key)
        if before is None and after is not None:
            lines.append(f"{key}: new (v{after.version})")
        elif before is not None and after is None:
            lines.append(f"{key}: removed")
        elif before is not None and after is not None and before.version != after.version:
            lines.append(f"{key}: v{before.version} -> v{after.version}")
    return lines
