"""The setup's shared answer checks, and writing the config only after it has been shown to load (FR-OPS-1)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from il2ks.config import ConfigError
from il2ks.ops.admin import read_password
from il2ks.ops.setup import (
    SetupAnswers,
    apply_answers,
    normalize_domain,
    normalize_email,
    write_config_file,
)

ENV = {"IL2KS_DATA_DIR": ""}
NOW = datetime(2026, 10, 4, 12, 0, 0)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Stats.Example.com", "stats.example.com"),
        ("https://Stats.Example.com/path", "stats.example.com"),
        ("  203.0.113.7 ", "203.0.113.7"),
        ("", ""),
    ],
)
def test_normalize_domain_accepts(text: str, expected: str) -> None:
    assert normalize_domain(text) == expected


@pytest.mark.parametrize("text", ["stats example.com", "stats.example.com:8443", "a b", "-x.com", "x" * 254, 'a"b.com'])
def test_normalize_domain_rejects(text: str) -> None:
    with pytest.raises(ValueError, match="not a domain"):
        normalize_domain(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("admin@example.com", "admin@example.com"),
        ("  Me.Name+tag@mail.example.org ", "Me.Name+tag@mail.example.org"),
        ("", ""),
    ],
)
def test_normalize_email_accepts(text: str, expected: str) -> None:
    assert normalize_email(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "no-at-sign",
        "a@b",
        "two words@example.com",
        'quote"@example.com',
        "a@example.com\nbcc@evil.test",
        "<a@example.com>",
        "a@example.com, b@example.com",
        "a@" + "x" * 250 + ".com",
    ],
)
def test_normalize_email_rejects(text: str) -> None:
    """It ends up in il2ks.toml and Caddy's configuration: nothing that could break out of a value may pass."""
    with pytest.raises(ValueError, match="not an e-mail"):
        normalize_email(text)


def test_a_config_that_does_not_load_never_replaces_the_working_one(tmp_path: Path) -> None:
    target = tmp_path / "il2ks.toml"
    target.write_text("[web]\nport = 8123\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        write_config_file(target, "[web]\nport = banana\n", ENV, now=lambda: NOW)
    assert target.read_text(encoding="utf-8") == "[web]\nport = 8123\n"  # untouched
    assert list(tmp_path.iterdir()) == [target]  # no .tmp left behind, and no backup of a file that was not replaced


def test_a_config_that_is_not_toml_is_refused_too(tmp_path: Path) -> None:
    target = tmp_path / "il2ks.toml"
    with pytest.raises(ConfigError):
        write_config_file(target, "this is [not toml", ENV, now=lambda: NOW)
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_a_good_config_replaces_the_old_one_and_keeps_a_backup(tmp_path: Path) -> None:
    target = tmp_path / "il2ks.toml"
    target.write_text("[web]\nport = 8123\n", encoding="utf-8")
    backup = write_config_file(target, "[web]\nport = 8124\n", ENV, now=lambda: NOW)
    assert target.read_text(encoding="utf-8") == "[web]\nport = 8124\n"
    assert backup is not None
    assert backup.read_text(encoding="utf-8") == "[web]\nport = 8123\n"
    assert not target.with_name("il2ks.toml.tmp").exists()


def test_apply_answers_leaves_the_existing_config_alone_when_an_environment_override_clashes(tmp_path: Path) -> None:
    """The temporary file is loaded with the real environment: an override that makes it invalid is found before the
    replace, not after."""
    target = tmp_path / "il2ks.toml"
    target.write_text("[web]\nport = 8123\n", encoding="utf-8")
    answers = SetupAnswers(data_dir=tmp_path / "data", timezone="UTC", https_mode="external")
    with pytest.raises(ConfigError):
        apply_answers(answers, target, {"IL2KS_WEB_PORT": "banana"}, now=lambda: NOW)
    assert target.read_text(encoding="utf-8") == "[web]\nport = 8123\n"
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == ["il2ks.toml"]


def test_apply_answers_writes_and_loads_a_valid_config(tmp_path: Path) -> None:
    target = tmp_path / "il2ks.toml"
    answers = SetupAnswers(
        data_dir=tmp_path / "data", timezone="Europe/Berlin", https_mode="caddy", domain="stats.example.com"
    )
    applied = apply_answers(answers, target, {}, now=lambda: NOW)
    assert applied.target == target
    assert applied.config.https.domain == "stats.example.com"
    assert applied.config.timezone_name == "Europe/Berlin"
    assert applied.config.source == target
    assert not target.with_name("il2ks.toml.tmp").exists()


def test_the_password_file_may_carry_a_utf8_byte_order_mark(tmp_path: Path) -> None:
    """The Windows installer writes the password with Inno's UTF-8 writer, which may add a BOM and a line break."""
    file = tmp_path / "pw.txt"
    file.write_bytes("﻿Korrekt-Pferd-Batterie-9\r\n".encode())
    assert read_password({}, file) == "Korrekt-Pferd-Batterie-9"
    file.write_bytes(b"Plain-pass-9\n")
    assert read_password({}, file) == "Plain-pass-9"
