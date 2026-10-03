"""The Django secret key (NFR-SEC-2): configured, or generated once into `<data dir>/secret_key.txt`.

Like the server UID (TD-17) it is created on first use and then kept, so login cookies survive restarts. Only the
web server needs a real key: `il2ks web` / `il2ks run` create the file before Django starts; other commands (`manage`,
`ingest`) run fine on the insecure development key if no key exists yet, and never sign anything with it.
"""

import contextlib
import os
import secrets
from pathlib import Path

from il2ks.config import Config

SECRET_KEY_FILE = "secret_key.txt"
DEV_SECRET_KEY = "dev-only-insecure-key-change-me"
"""The placeholder key. `il2ks web` refuses to serve with it unless debug mode is on."""


def secret_key_path(data_dir: Path) -> Path:
    return data_dir / SECRET_KEY_FILE


def read_stored_secret_key(data_dir: Path) -> str:
    """The key in `secret_key.txt`, or "" if there is no such file (or it is empty)."""
    try:
        return secret_key_path(data_dir).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def ensure_secret_key(data_dir: Path) -> str:
    """The stored key, generating it on first use. The file is made private to its owner where the OS allows."""
    existing = read_stored_secret_key(data_dir)
    if existing:
        return existing
    key = secrets.token_urlsafe(50)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = secret_key_path(data_dir)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"{key}\n")
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return key


def secret_key_for(cfg: Config) -> str:
    """What Django should use: `[web] secret_key`, else the stored key, else the development placeholder."""
    return cfg.web.secret_key or read_stored_secret_key(cfg.data_dir) or DEV_SECRET_KEY
