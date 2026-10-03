"""The SQLite file as a file: what backup, restore and doctor need to know without starting Django.

Everything here opens the database read-only or through SQLite's online backup API, so a running `watch` or web process
is never disturbed, and a missing database is never created by looking at it.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

type Migration = tuple[str, str]
"""`(app label, migration name)`, the rows of Django's `django_migrations` table."""


def _open_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)


def applied_migrations(path: Path) -> frozenset[Migration] | None:
    """The migrations recorded in the database file, or None if it is missing, empty or not a Django database."""
    if not path.is_file():
        return None
    try:
        with closing(_open_readonly(path)) as conn:
            rows = conn.execute("SELECT app, name FROM django_migrations").fetchall()
    except sqlite3.Error:
        return None
    return frozenset((str(app), str(name)) for app, name in rows) or None


def latest_migrations(applied: frozenset[Migration]) -> dict[str, str]:
    """The newest applied migration of each app (names start with a sequence number, so the maximum is the newest)."""
    latest: dict[str, str] = {}
    for app, name in sorted(applied):
        latest[app] = name
    return latest


def snapshot_database(source: Path, target: Path) -> None:
    """Write a consistent copy of the live database with SQLite's online backup API (never a file copy).

    Safe while other processes write: SQLite restarts the copy if a page changes underneath it."""
    with closing(_open_readonly(source)) as src, closing(sqlite3.connect(target)) as dst:
        dst.execute("PRAGMA synchronous=OFF")  # the copy is a temporary file that goes into a zip; skip the fsyncs
        src.backup(dst)


def integrity_problems(path: Path) -> list[str]:
    """What `PRAGMA integrity_check` reports; an empty list means the file is sound."""
    try:
        with closing(_open_readonly(path)) as conn:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.Error as exc:
        return [str(exc)]
    messages = [str(row[0]) for row in rows]
    return [] if messages == ["ok"] else messages
