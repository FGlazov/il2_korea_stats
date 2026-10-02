"""Anonymize a mission log for committed test fixtures (doc 08).

Replaces account/profile UUIDs with deterministic fake UUIDs, player nicknames with Player001..., and drops skin names
(custom skins can contain names). Mapping is by order of first appearance, so the output is stable for the same input.
"""

import re
import zipfile
from pathlib import Path

UUID_KEYS = ("IDS", "LOGIN", "USERID", "USERNICKID")
_UUID_RE = re.compile(r"\b(" + "|".join(UUID_KEYS) + r"):([0-9a-fA-F-]{36})")
_NAME_RE = re.compile(r"( AType:10 .* NAME:)(.*?)( TYPE:)")
_SKIN_RE = re.compile(r"( SKIN:)(.*?)( WM:)")
FAKE_UUID_PREFIX = "00000000-0000-4000-8000-"


class Anonymizer:
    def __init__(self) -> None:
        self._uuids: dict[str, str] = {}
        self._names: dict[str, str] = {}

    def _uuid(self, real: str) -> str:
        if real not in self._uuids:
            self._uuids[real] = f"{FAKE_UUID_PREFIX}{len(self._uuids) + 1:012d}"
        return self._uuids[real]

    def _name(self, real: str) -> str:
        if real not in self._names:
            self._names[real] = f"Player{len(self._names) + 1:03d}"
        return self._names[real]

    def line(self, line: str) -> str:
        line = _UUID_RE.sub(lambda m: f"{m.group(1)}:{self._uuid(m.group(2).lower())}", line)
        line = _NAME_RE.sub(lambda m: f"{m.group(1)}{self._name(m.group(2))}{m.group(3)}", line)
        return _SKIN_RE.sub(lambda m: f"{m.group(1)}{m.group(3)}", line)


def read_mission_text(source: Path) -> str:
    if source.suffix == ".zip":
        with zipfile.ZipFile(source) as zf:
            return zf.read(zf.namelist()[0]).decode("utf-8", errors="replace")
    return source.read_text(encoding="utf-8", errors="replace")


def anonymize_text(text: str) -> str:
    anonymizer = Anonymizer()
    return "".join(anonymizer.line(line) for line in text.splitlines(keepends=True))


def anonymize_file(source: Path, target: Path) -> None:
    text = anonymize_text(read_mission_text(source))
    inner = target.name.removesuffix(".zip")
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr(inner, text)
