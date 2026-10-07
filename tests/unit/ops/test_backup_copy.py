"""`[backup] copy_to` and `copy_archive`: the second copy of the backups and of the mission archive, its retention,
its failures (logged and shown in doctor, never failing the backup) and the config keys."""

from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from il2ks.config import BackupConfig, Config
from il2ks.ops import backup, checks
from il2ks.ops.doctor import Level
from tests.ops_helpers import make_instance

T = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)


def with_copy(tmp_path: Path, *, keep: int = 10, archive: bool = False) -> tuple[Config, Path]:
    second = tmp_path / "other-drive"
    cfg = replace(make_instance(tmp_path), backup=BackupConfig(keep=keep, copy_to=second, copy_archive=archive))
    return cfg, second


def names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if p.is_file())


def put_archive(cfg: Config, rel: str, content: bytes) -> Path:
    path = cfg.archive_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_config_reads_the_two_keys(tmp_path: Path) -> None:
    where = (tmp_path / "x").as_posix()
    cfg = make_instance(tmp_path, extra_toml=f'[backup]\ncopy_to = "{where}"\ncopy_archive = true\n')
    assert cfg.backup.copy_to == tmp_path / "x"
    assert cfg.backup.copy_archive is True
    assert make_instance(tmp_path / "other").backup == BackupConfig()
    assert BackupConfig().copy_to is None
    assert BackupConfig().copy_archive is False


def test_a_backup_is_copied_to_the_second_folder(tmp_path: Path) -> None:
    cfg, second = with_copy(tmp_path)
    made = backup.create_backup(cfg, now=lambda: T)
    assert names(second) == [made.name]
    assert (second / made.name).read_bytes() == made.read_bytes()
    assert backup.read_copy_status(cfg)["backups"].ok


def test_nothing_is_copied_when_no_second_folder_is_set(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path)
    backup.create_backup(cfg, now=lambda: T)
    assert backup.copy_to_second_folder(cfg) is None
    assert backup.mirror_archive(cfg) is None
    assert backup.read_copy_status(cfg) == {}


def test_retention_applies_to_the_copy_too(tmp_path: Path) -> None:
    cfg, second = with_copy(tmp_path, keep=2)
    made = [backup.create_backup(cfg, now=lambda i=i: T + timedelta(hours=i)) for i in range(4)]
    assert names(second) == sorted(p.name for p in made[-2:])
    assert names(cfg.backup_dir) == names(second)


def test_a_second_folder_that_was_offline_catches_up_with_the_next_backup(tmp_path: Path) -> None:
    cfg, second = with_copy(tmp_path)
    second.write_text("a file where the folder should be", encoding="utf-8")  # the "share" is broken
    first = backup.create_backup(cfg, now=lambda: T)
    assert not backup.read_copy_status(cfg)["backups"].ok
    second.unlink()
    again = backup.create_backup(cfg, now=lambda: T + timedelta(hours=1))
    assert names(second) == sorted([first.name, again.name])
    assert backup.read_copy_status(cfg)["backups"].ok


def test_a_failing_copy_never_fails_the_backup(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    cfg, second = with_copy(tmp_path)
    second.write_text("not a folder", encoding="utf-8")
    made = backup.create_backup(cfg, now=lambda: T)
    assert made.is_file()
    status = backup.read_copy_status(cfg)["backups"]
    assert not status.ok
    assert "could not copy" in status.detail
    assert "copy_to" in caplog.text


def test_copy_to_the_backup_folder_itself_is_refused(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path)
    cfg = replace(cfg, backup=BackupConfig(copy_to=cfg.backup_dir))
    made = backup.create_backup(cfg, now=lambda: T)
    assert made.is_file()
    assert "itself" in backup.read_copy_status(cfg)["backups"].detail


def test_the_archive_is_mirrored_incrementally(tmp_path: Path) -> None:
    cfg, second = with_copy(tmp_path, archive=True)
    one = put_archive(cfg, "2026/10/a.txt.zip", b"one")
    put_archive(cfg, "2026/10/b.txt.zip", b"two")
    outcome = backup.mirror_archive(cfg)
    assert outcome is not None
    assert outcome.ok
    assert outcome.detail.startswith("2 ")
    mirror = second / "archive" / "2026" / "10"
    assert (mirror / "a.txt.zip").read_bytes() == b"one"
    again = backup.mirror_archive(cfg)  # nothing new: nothing copied
    assert again is not None
    assert again.detail.startswith("0 ")
    put_archive(cfg, "2026/11/c.txt.zip", b"three")  # a new file and a changed one are copied, the untouched not
    one.write_bytes(b"one, longer")
    marker = (mirror / "b.txt.zip").stat().st_mtime_ns
    third = backup.mirror_archive(cfg)
    assert third is not None
    assert third.detail.startswith("2 ")
    assert (second / "archive" / "2026" / "11" / "c.txt.zip").read_bytes() == b"three"
    assert (mirror / "a.txt.zip").read_bytes() == b"one, longer"
    assert (mirror / "b.txt.zip").stat().st_mtime_ns == marker


def test_the_mirror_never_deletes(tmp_path: Path) -> None:
    cfg, second = with_copy(tmp_path, archive=True)
    gone = put_archive(cfg, "2026/10/a.txt.zip", b"one")
    backup.mirror_archive(cfg)
    gone.unlink()
    backup.mirror_archive(cfg)
    assert (second / "archive" / "2026" / "10" / "a.txt.zip").is_file()


def test_the_mirror_is_off_without_copy_archive(tmp_path: Path) -> None:
    cfg, second = with_copy(tmp_path, archive=False)
    put_archive(cfg, "2026/10/a.txt.zip", b"one")
    assert backup.mirror_archive(cfg) is None
    assert not (second / "archive").exists()


def test_a_failing_mirror_stops_early_and_is_recorded(tmp_path: Path) -> None:
    cfg, second = with_copy(tmp_path, archive=True)
    second.mkdir()
    (second / "archive").write_text("blocks the folder", encoding="utf-8")
    for i in range(10):
        put_archive(cfg, f"2026/10/{i}.txt.zip", b"x")
    outcome = backup.mirror_archive(cfg)
    assert outcome is not None
    assert not outcome.ok
    assert not backup.read_copy_status(cfg)["archive"].ok


def test_the_daily_backup_mirrors_the_archive(tmp_path: Path) -> None:
    cfg, second = with_copy(tmp_path, archive=True)
    put_archive(cfg, "2026/10/a.txt.zip", b"one")
    assert backup.backup_if_due(cfg, T) is not None
    assert (second / "archive" / "2026" / "10" / "a.txt.zip").is_file()


def test_a_share_that_rounds_modification_times_does_not_get_everything_recopied(tmp_path: Path) -> None:
    cfg, second = with_copy(tmp_path, archive=True)
    src = put_archive(cfg, "2026/10/a.txt.zip", b"one")
    backup.mirror_archive(cfg)
    dest = second / "archive" / "2026" / "10" / "a.txt.zip"
    stamp = src.stat().st_mtime
    os.utime(dest, (stamp - 1, stamp - 1))
    again = backup.mirror_archive(cfg)
    assert again is not None
    assert again.detail.startswith("0 ")


# --- doctor ---------------------------------------------------------------------------------------------------------


def test_doctor_is_silent_without_a_second_folder(tmp_path: Path) -> None:
    assert list(checks.backup_copy_check(make_instance(tmp_path))) == []


def test_doctor_warns_before_the_first_copy(tmp_path: Path) -> None:
    cfg, _ = with_copy(tmp_path)
    [finding] = checks.backup_copy_check(cfg)
    assert finding.level is Level.WARN


def test_doctor_is_ok_after_a_copy(tmp_path: Path) -> None:
    cfg, _ = with_copy(tmp_path, archive=True)
    put_archive(cfg, "2026/10/a.txt.zip", b"one")
    backup.create_backup(cfg, now=lambda: T)
    backup.mirror_archive(cfg)
    assert [f.level for f in checks.backup_copy_check(cfg)] == [Level.OK, Level.OK]


def test_doctor_shows_a_failed_copy(tmp_path: Path) -> None:
    cfg, second = with_copy(tmp_path)
    second.write_text("broken", encoding="utf-8")
    backup.create_backup(cfg, now=lambda: T)
    [finding] = checks.backup_copy_check(cfg)
    assert finding.level is Level.WARN
    assert str(second) in finding.title
    assert "could not copy" in finding.detail


# --- review 0.2.0 M6: the copy never stalls the writer, installs do not share a folder ---------------------------------


def _slow_copy(monkeypatch: pytest.MonkeyPatch, release: threading.Event) -> threading.Event:
    """Make every copy to the second folder block until `release` is set (an offline SMB share)."""
    started = threading.Event()
    real = backup._copy_file  # pyright: ignore[reportPrivateUsage]

    def slow(source: Path, dest: Path) -> None:
        started.set()
        release.wait(timeout=10)
        real(source, dest)

    monkeypatch.setattr(backup, "_copy_file", slow)
    return started


def test_create_backup_does_not_wait_for_a_slow_second_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The writer lock is held while a backup is made (also pre-wipe, pre-migrate): a share that blocks for a minute
    per call must not hold it."""
    cfg, second = with_copy(tmp_path)
    release = threading.Event()
    started = _slow_copy(monkeypatch, release)
    began = time.monotonic()
    made = backup.create_backup(cfg, now=lambda: T)
    took = time.monotonic() - began
    release.set()
    backup.wait_for_copy()
    assert took < 3, "create_backup waited for the copy"
    assert started.is_set()
    assert (second / str(cfg.server_uid) / made.name).is_file()


def test_the_daily_backup_does_not_wait_for_the_archive_mirror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`watch` runs the daily backup in its loop: a first mirror of a big archive must not stop ingestion."""
    cfg, second = with_copy(tmp_path, archive=True)
    put_archive(cfg, "2026/10/a.txt.zip", b"one")
    release = threading.Event()
    _slow_copy(monkeypatch, release)
    began = time.monotonic()
    assert backup.backup_if_due(cfg, T) is not None
    took = time.monotonic() - began
    release.set()
    backup.wait_for_copy()
    assert took < 3, "backup_if_due waited for the copy"
    assert (second / str(cfg.server_uid) / "archive" / "2026" / "10" / "a.txt.zip").is_file()


def test_the_copy_goes_into_a_folder_of_the_server_uid(tmp_path: Path) -> None:
    cfg, second = with_copy(tmp_path, archive=True)
    put_archive(cfg, "2026/10/a.txt.zip", b"one")
    made = backup.create_backup(cfg, now=lambda: T)
    backup.mirror_archive(cfg)
    backup.wait_for_copy()
    mine = second / str(cfg.server_uid)
    assert names(mine) == [made.name]
    assert (mine / "archive" / "2026" / "10" / "a.txt.zip").is_file()
    assert names(second) == []  # nothing loose in the shared folder itself


def test_two_installs_sharing_a_folder_do_not_rotate_each_others_backups(tmp_path: Path) -> None:
    one, second = with_copy(tmp_path / "one", keep=1)
    other = replace(make_instance(tmp_path / "two"), server_uid=uuid.uuid4(), backup=one.backup)
    first = backup.create_backup(one, now=lambda: T)
    backup.wait_for_copy()
    backup.create_backup(other, now=lambda: T + timedelta(hours=1))
    backup.wait_for_copy()
    assert names(second / str(one.server_uid)) == [first.name]
    assert len(names(second / str(other.server_uid))) == 1


def test_a_failing_copystat_after_the_data_was_written_is_tolerated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NAS shares refuse to set times or attributes on a file that was written fine."""
    cfg, second = with_copy(tmp_path)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("cannot set attributes on this share")

    monkeypatch.setattr(shutil, "copystat", refuse)
    made = backup.create_backup(cfg, now=lambda: T)
    backup.wait_for_copy()
    assert (second / str(cfg.server_uid) / made.name).read_bytes() == made.read_bytes()
    assert backup.read_copy_status(cfg)["backups"].ok


def test_stale_tmp_files_in_the_second_folder_are_cleaned_up(tmp_path: Path) -> None:
    cfg, second = with_copy(tmp_path)
    mine = second / str(cfg.server_uid)
    mine.mkdir(parents=True)
    stale = mine / "il2ks-backup-20260101-000000.zip.tmp"
    fresh = mine / "il2ks-backup-20260102-000000.zip.tmp"
    stale.write_bytes(b"left by a crash")
    fresh.write_bytes(b"being written by another process")
    old = time.time() - 3 * 3600
    os.utime(stale, (old, old))
    backup.create_backup(cfg, now=lambda: T)
    backup.wait_for_copy()
    assert not stale.exists()
    assert fresh.exists()
