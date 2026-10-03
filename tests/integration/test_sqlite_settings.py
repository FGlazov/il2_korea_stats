"""SQLite settings (TD-04): WAL, IMMEDIATE transactions, and a backup taken while a write transaction is open."""

import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from django.conf import settings
from django.db import ConnectionHandler
from pytest_django.plugin import DjangoDbBlocker

from il2ks.ops import backup
from tests.ops_helpers import make_instance, read_notes


@pytest.fixture(autouse=True)
def _own_database_connections(django_db_blocker: DjangoDbBlocker) -> Iterator[None]:
    """These tests open their own file databases outside the test database, which pytest-django blocks by default."""
    with django_db_blocker.unblock():
        yield


def sqlite_connections(db_file: Path) -> ConnectionHandler:
    """A fresh handler over a file database with the project's SQLite settings (the test database is in memory)."""
    sqlite = next(db for db in settings.DATABASES.values() if db["ENGINE"] == "django.db.backends.sqlite3")
    return ConnectionHandler({"default": {**sqlite, "NAME": db_file}})


@pytest.mark.sqlite_only
def test_sqlite_runs_in_wal_mode_with_immediate_transactions(tmp_path: Path) -> None:
    """Readers must not hit 'database is locked' while an ingest commits, and a lock upgrade must wait."""
    connections = sqlite_connections(tmp_path / "wal.sqlite3")
    try:
        conn = connections["default"]
        with conn.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode")
            assert cursor.fetchone() == ("wal",)
            cursor.execute("PRAGMA synchronous")
            assert cursor.fetchone() == (1,)  # NORMAL
            cursor.execute("PRAGMA busy_timeout")
            assert cursor.fetchone() == (20000,)
        assert settings.DATABASES["default"]["OPTIONS"]["transaction_mode"] == "IMMEDIATE"
    finally:
        connections.close_all()


def test_a_reader_is_not_blocked_by_an_open_write_transaction(tmp_path: Path) -> None:
    db_file = tmp_path / "wal.sqlite3"
    writer_side = sqlite_connections(db_file)
    reader_side = sqlite_connections(db_file)
    try:
        with writer_side["default"].cursor() as writer:
            writer.execute("CREATE TABLE t (x INTEGER)")
            writer.execute("INSERT INTO t VALUES (1)")
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("INSERT INTO t VALUES (2)")  # uncommitted, holds the write lock
            with reader_side["default"].cursor() as reader:
                reader.execute("SELECT x FROM t")
                assert reader.fetchall() == [(1,)]
            writer.execute("ROLLBACK")
    finally:
        writer_side.close_all()
        reader_side.close_all()


def test_a_backup_taken_during_a_write_transaction_holds_committed_data_only(tmp_path: Path) -> None:
    """The snapshot goes through SQLite's backup API, so committed rows still in the `-wal` file are in it and the
    open, uncommitted write is not."""
    cfg = make_instance(tmp_path)
    connections = sqlite_connections(cfg.db_path)
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("INSERT INTO notes (text) VALUES ('committed, in the wal')")
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute("INSERT INTO notes (text) VALUES ('uncommitted')")
            zip_path = backup.create_backup(cfg)
            cursor.execute("ROLLBACK")
    finally:
        connections.close_all()
    with zipfile.ZipFile(zip_path) as zf:
        zf.extract("il2ks.sqlite3", tmp_path / "out")
    assert read_notes(tmp_path / "out" / "il2ks.sqlite3") == ["first", "committed, in the wal"]
