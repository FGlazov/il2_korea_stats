"""Anonymize a mission log for committed test fixtures (doc 08).

Account/profile UUIDs and nicknames are replaced by a **keyed hash** (HMAC-SHA256), so:
- the same real player gets the same fake ID in every fixture file (fixtures can be ingested together), and
- nobody can re-identify a player by hashing a known UUID, because the key is secret and never committed.
Skin names are dropped (custom skins can contain names).

The key comes from IL2KS_ANON_KEY, or from `.il2ks-anon-key` in the working directory
(gitignored, created on first use).
"""

import hashlib
import hmac
import os
import re
import secrets
import zipfile
from pathlib import Path

UUID_KEYS = ("IDS", "LOGIN", "USERID", "USERNICKID")
_UUID_RE = re.compile(r"\b(" + "|".join(UUID_KEYS) + r"):([0-9a-fA-F-]{36})")
_NAME_RE = re.compile(r"( AType:10 .* NAME:)(.*?)( TYPE:)")
_SKIN_RE = re.compile(r"( SKIN:)(.*?)( WM:)")
FAKE_UUID_PREFIX = "00000000-0000-4000-8000-"
FAKE_NAME_RE = re.compile(r"Player-[0-9a-f]{6}")
KEY_FILE = Path(".il2ks-anon-key")


def load_key() -> bytes:
    env = os.environ.get("IL2KS_ANON_KEY")
    if env:
        return env.encode()
    if not KEY_FILE.exists():
        KEY_FILE.write_text(secrets.token_hex(32), encoding="utf-8")
    return KEY_FILE.read_text(encoding="utf-8").strip().encode()


class Anonymizer:
    def __init__(self, key: bytes) -> None:
        self._key = key

    def _digest(self, kind: str, value: str) -> str:
        return hmac.new(self._key, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()

    def fake_uuid(self, real: str) -> str:
        return FAKE_UUID_PREFIX + self._digest("uuid", real.lower())[:12]

    def fake_name(self, real: str) -> str:
        return "Player-" + self._digest("name", real)[:6]

    def line(self, line: str) -> str:
        line = _UUID_RE.sub(lambda m: f"{m.group(1)}:{self.fake_uuid(m.group(2))}", line)
        line = _NAME_RE.sub(lambda m: f"{m.group(1)}{self.fake_name(m.group(2))}{m.group(3)}", line)
        return _SKIN_RE.sub(lambda m: f"{m.group(1)}{m.group(3)}", line)


def read_mission_text(source: Path) -> str:
    if source.suffix == ".zip":
        with zipfile.ZipFile(source) as zf:
            return zf.read(zf.namelist()[0]).decode("utf-8", errors="replace")
    return source.read_text(encoding="utf-8", errors="replace")


def anonymize_text(text: str, key: bytes) -> str:
    anonymizer = Anonymizer(key)
    return "".join(anonymizer.line(line) for line in text.splitlines(keepends=True))


def anonymize_file(source: Path, target: Path) -> None:
    text = anonymize_text(read_mission_text(source), load_key())
    inner = target.name.removesuffix(".zip")
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr(inner, text)
