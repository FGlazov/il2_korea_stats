"""Builders for the ops tests: a small but real instance (config file, SQLite database file, custom/ and media/) in a
temp folder, and a scripted `Prompter`. Nothing here touches the user's real data or system configuration."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from il2ks.config import Config, load_config
from il2ks.ops.template import toml_string

MIGRATIONS = [("il2_db", "0001_initial"), ("il2_db", "0002_more"), ("auth", "0001_initial")]
LATEST = {"il2_db": "0002_more", "auth": "0001_initial"}


def returning[T](value: T) -> Callable[..., T]:
    """A stand-in for any function that ignores its arguments and answers `value` (for `monkeypatch.setattr`)."""

    def stand_in(*args: object, **kwargs: object) -> T:
        return value

    return stand_in


def recording(log: list[str], entry: str) -> Callable[..., None]:
    """A stand-in for any function that only notes in `log` that it was called."""

    def stand_in(*args: object, **kwargs: object) -> None:
        log.append(entry)

    return stand_in


def write_db(path: Path, notes: list[str] | None = None, migrations: list[tuple[str, str]] | None = None) -> None:
    """A SQLite file shaped like a migrated Django database: `django_migrations` plus one data table."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE django_migrations (id INTEGER PRIMARY KEY, app TEXT, name TEXT, applied TEXT)")
        conn.executemany(
            "INSERT INTO django_migrations (app, name, applied) VALUES (?, ?, '2026-01-01')",
            MIGRATIONS if migrations is None else migrations,
        )
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, text TEXT)")
        conn.executemany("INSERT INTO notes (text) VALUES (?)", [(n,) for n in notes or ["first"]])
        conn.commit()


def read_notes(path: Path) -> list[str]:
    with closing(sqlite3.connect(path)) as conn:
        return [row[0] for row in conn.execute("SELECT text FROM notes ORDER BY id")]


def make_instance(tmp_path: Path, *, with_db: bool = True, extra_toml: str = "") -> Config:
    """`<tmp>/il2ks.toml` pointing at `<tmp>/data` holding a database, `custom/`, `media/` and a server ID."""
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    config_file = tmp_path / "il2ks.toml"
    config_file.write_text(
        f'data_dir = {toml_string(data)}\n[server]\ntimezone = "UTC"\nuid = "11111111-2222-3333-4444-555555555555"\n'
        + extra_toml,
        encoding="utf-8",
    )
    (data / "server_uid.txt").write_text("11111111-2222-3333-4444-555555555555\n", encoding="utf-8")
    (data / "custom" / "templates").mkdir(parents=True)
    (data / "custom" / "templates" / "base.html").write_text("<p>mine</p>", encoding="utf-8")
    (data / "media").mkdir()
    (data / "media" / "logo.png").write_bytes(b"\x89PNG-fake")
    if with_db:
        write_db(data / "il2ks.sqlite3")
    return load_config(config_file, {})


@dataclass(slots=True)
class ScriptedPrompter:
    """Answers questions from a list, in order; `None` in the list means "just press Enter" (the default)."""

    answers: list[str | None]
    secrets: list[str] = field(default_factory=list[str])
    confirms: list[bool | None] = field(default_factory=list[bool | None])
    said: list[str] = field(default_factory=list[str])
    asked: list[str] = field(default_factory=list[str])

    def say(self, text: str = "") -> None:
        self.said.append(text)

    def ask(self, question: str, default: str = "") -> str:
        self.asked.append(f"{question} [{default}]")
        answer = self.answers.pop(0) if self.answers else None
        return default if answer is None else answer

    def ask_secret(self, question: str) -> str:
        self.asked.append(question)
        return self.secrets.pop(0)

    def confirm(self, question: str, default: bool) -> bool:
        self.asked.append(f"{question} [{default}]")
        answer = self.confirms.pop(0) if self.confirms else None
        return default if answer is None else answer

    @property
    def transcript(self) -> str:
        return "\n".join(self.said)
