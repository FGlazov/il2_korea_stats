"""Backups and restore (FR-OPS-6): contents, consistency, rotation, automatic triggers, round trip, refusals."""

from __future__ import annotations

import json
import socket
import sqlite3
import zipfile
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from il2ks import __version__
from il2ks.config import BackupConfig, Config
from il2ks.ingest.lock import LockBusyError, WriterLock
from il2ks.ops import backup
from il2ks.ops.backup import BackupError
from tests.ops_helpers import LATEST, make_instance, read_notes, write_db

T = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)


def zip_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        return zf.namelist()


def manifest_of(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path) as zf:
        data: dict[str, object] = json.loads(zf.read("manifest.json"))
        return data


def test_a_backup_holds_the_admin_state_and_a_manifest(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path)
    (cfg.data_dir / "secret_key.txt").write_text("k", encoding="utf-8")
    (cfg.data_dir / "archive" / "2026" / "10").mkdir(parents=True)
    (cfg.data_dir / "archive" / "2026" / "10" / "m.txt.zip").write_bytes(b"big")

    path = backup.create_backup(cfg, "manual", now=lambda: T)

    assert path == cfg.backup_dir / "il2ks-backup-20261001-120000.zip"
    names = zip_names(path)
    assert {
        "manifest.json",
        "il2ks.sqlite3",
        "il2ks.toml",
        "server_uid.txt",
        "secret_key.txt",
        "custom/templates/base.html",
        "media/logo.png",
    } <= set(names)
    assert not any(n.startswith("archive") for n in names)  # logs are large and kept on their own
    manifest = manifest_of(path)
    assert manifest["il2ks_version"] == __version__
    assert manifest["created_at"] == "2026-10-01T12:00:00+00:00"
    assert manifest["reason"] == "manual"
    assert manifest["migrations"] == LATEST
    assert manifest["server_uid"] == str(cfg.server_uid)


def test_optional_files_are_left_out_when_they_do_not_exist(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path)
    (cfg.data_dir / "server_uid.txt").unlink()
    names = zip_names(backup.create_backup(cfg, now=lambda: T))
    assert "server_uid.txt" not in names
    assert "secret_key.txt" not in names


def test_the_snapshot_is_a_consistent_copy_not_the_live_file(tmp_path: Path) -> None:
    """Committed data still sitting in the WAL (a plain file copy would lose it) is in the backup; an open,
    uncommitted write is not."""
    cfg = make_instance(tmp_path)
    live = sqlite3.connect(cfg.db_path)
    try:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("INSERT INTO notes (text) VALUES ('in the wal')")
        live.commit()
        live.execute("INSERT INTO notes (text) VALUES ('not committed')")  # transaction left open
        path = backup.create_backup(cfg, now=lambda: T)
    finally:
        live.close()
    with zipfile.ZipFile(path) as zf:
        zf.extract("il2ks.sqlite3", tmp_path / "out")
    assert read_notes(tmp_path / "out" / "il2ks.sqlite3") == ["first", "in the wal"]


def test_backing_up_without_a_database_says_what_to_do(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path, with_db=False)
    with pytest.raises(BackupError, match="il2ks setup"):
        backup.create_backup(cfg)
    assert not list(cfg.backup_dir.glob("*")) if cfg.backup_dir.exists() else True


def test_rotation_keeps_the_newest_n(tmp_path: Path) -> None:
    cfg = replace(make_instance(tmp_path), backup=BackupConfig(keep=3))
    for i in range(5):
        backup.create_backup(cfg, now=lambda i=i: T + timedelta(hours=i))
    kept = [p.name for p in backup.list_backups(cfg.backup_dir)]
    assert kept == [
        "il2ks-backup-20261001-140000.zip",
        "il2ks-backup-20261001-150000.zip",
        "il2ks-backup-20261001-160000.zip",
    ]


def test_rotation_ignores_files_that_are_not_ours(tmp_path: Path) -> None:
    cfg = replace(make_instance(tmp_path), backup=BackupConfig(keep=1))
    cfg.backup_dir.mkdir()
    (cfg.backup_dir / "notes.txt").write_text("mine", encoding="utf-8")
    backup.create_backup(cfg, now=lambda: T)
    backup.create_backup(cfg, now=lambda: T + timedelta(hours=1))
    assert (cfg.backup_dir / "notes.txt").is_file()
    assert len(backup.list_backups(cfg.backup_dir)) == 1


def test_two_backups_in_the_same_second_do_not_overwrite_each_other(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path)
    first = backup.create_backup(cfg, now=lambda: T)
    second = backup.create_backup(cfg, now=lambda: T)
    assert first != second
    assert first.is_file()
    assert second.is_file()


def test_a_failed_backup_leaves_no_half_written_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_instance(tmp_path)

    def boom(source: Path, target: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(backup, "snapshot_database", boom)
    with pytest.raises(OSError, match="disk full"):
        backup.create_backup(cfg)
    assert list(cfg.backup_dir.iterdir()) == []


# --- automatic triggers ---------------------------------------------------------------------------------------------


def test_daily_backup_is_made_when_none_exists(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path)
    made = backup.backup_if_due(cfg, T)
    assert made is not None
    assert manifest_of(made)["reason"] == "daily"


def test_daily_backup_waits_until_the_newest_is_a_day_old(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path)
    backup.create_backup(cfg, now=lambda: T)
    assert backup.backup_if_due(cfg, T + timedelta(hours=23)) is None
    assert backup.backup_if_due(cfg, T + timedelta(hours=25)) is not None


def test_daily_backup_can_be_switched_off(tmp_path: Path) -> None:
    cfg = replace(make_instance(tmp_path), backup=BackupConfig(daily=False))
    assert backup.backup_if_due(cfg, T) is None
    assert not cfg.backup_dir.exists()


def test_daily_backup_skips_when_there_is_no_database_yet(tmp_path: Path) -> None:
    assert backup.backup_if_due(make_instance(tmp_path, with_db=False), T) is None


def test_daily_backup_skips_while_another_writer_holds_the_lock(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path)
    with WriterLock(cfg.data_dir, "restore"):
        assert backup.backup_if_due(cfg, T) is None
    assert backup.backup_if_due(cfg, T) is not None


def test_backup_before_migration_only_when_there_is_data_to_protect(tmp_path: Path) -> None:
    fresh = make_instance(tmp_path / "fresh", with_db=False)
    assert backup.backup_before_migration(fresh) is None
    cfg = make_instance(tmp_path / "old")
    made = backup.backup_before_migration(cfg)
    assert made is not None
    assert manifest_of(made)["reason"] == "pre-migrate"


# --- restore --------------------------------------------------------------------------------------------------------


def test_restore_round_trip_brings_everything_back(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path)
    assert cfg.source is not None
    original_config = cfg.source.read_text(encoding="utf-8")
    zip_path = backup.create_backup(cfg, now=lambda: T)

    # The admin state changes after the backup ...
    with closing(sqlite3.connect(cfg.db_path)) as conn:
        conn.execute("INSERT INTO notes (text) VALUES ('after the backup')")
        conn.commit()
    (cfg.data_dir / "custom" / "templates" / "base.html").unlink()
    (cfg.data_dir / "custom" / "extra.txt").write_text("new", encoding="utf-8")
    (cfg.data_dir / "media" / "logo.png").write_bytes(b"other")
    cfg.source.write_text("# broken by hand\n", encoding="utf-8")

    result = backup.restore_backup(cfg, zip_path, cfg.source, now=lambda: T + timedelta(hours=1))

    assert read_notes(cfg.db_path) == ["first"]
    assert (cfg.data_dir / "custom" / "templates" / "base.html").read_text(encoding="utf-8") == "<p>mine</p>"
    assert not (cfg.data_dir / "custom" / "extra.txt").exists()
    assert (cfg.data_dir / "media" / "logo.png").read_bytes() == b"\x89PNG-fake"
    assert cfg.source.read_text(encoding="utf-8") == original_config
    assert result.config_written == cfg.source
    # ... and the state from just before the restore is kept in a safety backup (restorable in turn).
    assert result.safety_backup is not None
    assert manifest_of(result.safety_backup)["reason"] == "pre-restore"
    with zipfile.ZipFile(result.safety_backup) as zf:
        zf.extract("il2ks.sqlite3", tmp_path / "safety")
    assert read_notes(tmp_path / "safety" / "il2ks.sqlite3") == ["first", "after the backup"]
    assert not any(p.name.startswith("restore-") for p in cfg.data_dir.iterdir())  # staging folder cleaned up


def test_restore_leaves_no_stale_wal_files_next_to_the_restored_database(tmp_path: Path) -> None:
    """TD-04 (WAL mode): a `-wal`/`-shm` pair left by the replaced database would be applied to the restored file."""
    cfg = make_instance(tmp_path)
    zip_path = backup.create_backup(cfg, now=lambda: T)
    live = sqlite3.connect(cfg.db_path)
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("INSERT INTO notes (text) VALUES ('only in the live wal')")
    live.commit()
    wal, shm = (Path(f"{cfg.db_path}{suffix}") for suffix in ("-wal", "-shm"))
    stale = {wal: wal.read_bytes(), shm: shm.read_bytes()}
    live.close()  # a clean close checkpoints and removes the pair; put back what a crashed process would leave
    for path, content in stale.items():
        path.write_bytes(content)

    backup.restore_backup(cfg, zip_path, cfg.source or tmp_path / "il2ks.toml", now=lambda: T + timedelta(hours=1))

    assert read_notes(cfg.db_path) == ["first"]
    assert not wal.exists()
    assert not shm.exists()


def test_restore_into_a_fresh_install(tmp_path: Path) -> None:
    source = make_instance(tmp_path / "old")
    zip_path = backup.create_backup(source, now=lambda: T)
    fresh = make_instance(tmp_path / "new", with_db=False)
    assert fresh.source is not None
    target = tmp_path / "elsewhere" / "il2ks.toml"

    result = backup.restore_backup(fresh, zip_path, target)

    assert read_notes(fresh.db_path) == ["first"]
    assert result.safety_backup is None  # nothing there to protect
    assert target.is_file()
    assert result.restored_data_dir_differs  # the restored config names the old machine's data folder


def test_restore_refuses_while_the_writer_lock_is_held_and_changes_nothing(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path)
    zip_path = backup.create_backup(cfg, now=lambda: T)
    with closing(sqlite3.connect(cfg.db_path)) as conn:
        conn.execute("INSERT INTO notes (text) VALUES ('newer')")
        conn.commit()
    assert cfg.source is not None
    with WriterLock(cfg.data_dir, "watch"), pytest.raises(LockBusyError):
        backup.restore_backup(cfg, zip_path, cfg.source)
    assert read_notes(cfg.db_path) == ["first", "newer"]


def test_restore_rejects_a_damaged_zip(tmp_path: Path) -> None:
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"this is not a zip")
    with pytest.raises(BackupError, match="cannot read"):
        backup.read_manifest(bad)


def test_restore_rejects_a_zip_that_is_not_an_il2ks_backup(tmp_path: Path) -> None:
    other = tmp_path / "other.zip"
    with zipfile.ZipFile(other, "w") as zf:
        zf.writestr("hello.txt", "x")
    with pytest.raises(BackupError, match="not an il2ks backup"):
        backup.read_manifest(other)


def test_restore_rejects_entries_outside_the_data_folder(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path)
    good = backup.create_backup(cfg, now=lambda: T)
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(evil, "w") as dst:
        for name in src.namelist():
            dst.writestr(name, src.read(name))
        dst.writestr("custom/../../outside.txt", "x")
    with pytest.raises(BackupError, match="unexpected entry"):
        backup.read_manifest(evil)


def test_restore_refuses_a_backup_from_a_newer_il2ks(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path)
    good = backup.create_backup(cfg, now=lambda: T)
    newer = tmp_path / "newer.zip"
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(newer, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            if name == "manifest.json":
                manifest = json.loads(data)
                manifest["il2ks_version"] = "99.0.0"
                data = json.dumps(manifest).encode()
            dst.writestr(name, data)
    assert cfg.source is not None
    with pytest.raises(BackupError, match="upgrade il2ks"):
        backup.restore_backup(cfg, newer, cfg.source)


def test_restore_stops_before_touching_anything_if_the_backup_database_is_damaged(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path)
    good = backup.create_backup(cfg, now=lambda: T)
    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(broken, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            dst.writestr(name, data[: len(data) // 2] if name == "il2ks.sqlite3" else data)
    assert cfg.source is not None
    with pytest.raises(BackupError, match="damaged"):
        backup.restore_backup(cfg, broken, cfg.source)
    assert read_notes(cfg.db_path) == ["first"]


def test_the_web_process_is_noticed_by_its_port() -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = int(server.getsockname()[1])
        assert backup.web_probably_running(port)
    assert not backup.web_probably_running(port)


def test_backup_helpers_are_pure_functions_of_the_names(tmp_path: Path) -> None:
    assert backup.backup_time(Path("il2ks-backup-20261001-120000.zip")) == T
    assert backup.backup_time(Path("il2ks-backup-oops.zip")) is None
    assert backup.newest_backup_time(tmp_path / "nothing") is None
    write_db(tmp_path / "x.sqlite3")  # keep the helper import honest: a plain database is not a backup folder
    assert backup.list_backups(tmp_path) == []


def test_default_config_keeps_ten_backups_and_makes_daily_ones() -> None:
    assert BackupConfig() == BackupConfig(keep=10, daily=True)
    cfg: Config = make_cfg_defaults()
    assert cfg.backup.keep == 10


def make_cfg_defaults() -> Config:
    from il2ks.config import load_config

    return load_config(None, {"IL2KS_DATA_DIR": "x"}, create_server_uid=False)
