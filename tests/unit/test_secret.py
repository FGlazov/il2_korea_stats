"""The Django secret key (NFR-SEC-2): generated once, kept, configurable, never the placeholder when serving."""

import dataclasses
import os
import stat
import sys
from pathlib import Path

from il2ks.config import Config, WebConfig, load_config
from il2ks.serving.secret import (
    DEV_SECRET_KEY,
    SECRET_KEY_FILE,
    ensure_secret_key,
    read_stored_secret_key,
    secret_key_for,
)


def cfg(tmp_path: Path, key: str = "") -> Config:
    base = load_config(None, {"IL2KS_DATA_DIR": str(tmp_path / "data")}, create_server_uid=False)
    return dataclasses.replace(base, web=WebConfig(secret_key=key))


def test_generated_once_then_kept(tmp_path: Path) -> None:
    data = tmp_path / "data"
    first = ensure_secret_key(data)
    assert len(first) >= 50
    assert ensure_secret_key(data) == first
    assert (data / SECRET_KEY_FILE).read_text(encoding="utf-8").strip() == first
    assert read_stored_secret_key(data) == first


def test_two_installs_get_different_keys(tmp_path: Path) -> None:
    assert ensure_secret_key(tmp_path / "a") != ensure_secret_key(tmp_path / "b")


def test_an_empty_file_is_replaced(tmp_path: Path) -> None:
    (tmp_path / SECRET_KEY_FILE).write_text("\n", encoding="utf-8")
    assert ensure_secret_key(tmp_path)
    assert read_stored_secret_key(tmp_path)


def test_the_file_is_private_to_the_owner_on_posix(tmp_path: Path) -> None:
    ensure_secret_key(tmp_path)
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(tmp_path / SECRET_KEY_FILE).st_mode) == 0o600


def test_precedence_config_then_file_then_placeholder(tmp_path: Path) -> None:
    assert secret_key_for(cfg(tmp_path)) == DEV_SECRET_KEY  # nothing yet: the placeholder (never serves)
    ensure_secret_key(tmp_path / "data")
    stored = read_stored_secret_key(tmp_path / "data")
    assert secret_key_for(cfg(tmp_path)) == stored
    assert secret_key_for(cfg(tmp_path, "from-config")) == "from-config"


def test_reading_does_not_create_the_file(tmp_path: Path) -> None:
    secret_key_for(cfg(tmp_path))
    assert not (tmp_path / "data").exists()
