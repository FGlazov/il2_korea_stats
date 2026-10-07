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


@pytest.fixture(autouse=True)
def _copy_not_cancelled() -> None:
    backup._copy_cancel.clear()  # pyright: ignore[reportPrivateUsage]


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
    backup.wait_for_copy()
    assert names(second / str(cfg.server_uid)) == [made.name]
    assert (second / str(cfg.server_uid) / made.name).read_bytes() == made.read_bytes()
    assert backup.read_copy_status(cfg)["backups"].ok


def test_nothing_is_copied_when_no_second_folder_is_set(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path)
    backup.create_backup(cfg, now=lambda: T)
    backup.wait_for_copy()
    assert backup.copy_to_second_folder(cfg) is None
    assert backup.mirror_archive(cfg) is None
    assert backup.read_copy_status(cfg) == {}


def test_retention_applies_to_the_copy_too(tmp_path: Path) -> None:
    cfg, second = with_copy(tmp_path, keep=2)
    made = [backup.create_backup(cfg, now=lambda i=i: T + timedelta(hours=i)) for i in range(4)]
    backup.wait_for_copy()
    assert names(second / str(cfg.server_uid)) == sorted(p.name for p in made[-2:])
    assert names(cfg.backup_dir) == names(second / str(cfg.server_uid))


def test_a_second_folder_that_was_offline_catches_up_with_the_next_backup(tmp_path: Path) -> None:
    cfg, second = with_copy(tmp_path)
    second.write_text("a file where the folder should be", encoding="utf-8")  # the "share" is broken
    first = backup.create_backup(cfg, now=lambda: T)
    backup.wait_for_copy()
    assert not backup.read_copy_status(cfg)["backups"].ok
    second.unlink()
    again = backup.create_backup(cfg, now=lambda: T + timedelta(hours=1))
    backup.wait_for_copy()
    assert names(second / str(cfg.server_uid)) == sorted([first.name, again.name])
    assert backup.read_copy_status(cfg)["backups"].ok


def test_a_failing_copy_never_fails_the_backup(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    cfg, second = with_copy(tmp_path)
    second.write_text("not a folder", encoding="utf-8")
    made = backup.create_backup(cfg, now=lambda: T)
    backup.wait_for_copy()
    assert made.is_file()
    status = backup.read_copy_status(cfg)["backups"]
    assert not status.ok
    assert "could not copy" in status.detail
    assert "copy_to" in caplog.text


def test_copy_to_the_backup_folder_itself_is_refused(tmp_path: Path) -> None:
    cfg = make_instance(tmp_path)
    cfg = replace(cfg, backup=BackupConfig(copy_to=cfg.backup_dir))
    made = backup.create_backup(cfg, now=lambda: T)
    backup.wait_for_copy()
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
    mirror = second / str(cfg.server_uid) / "archive" / "2026" / "10"
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
    assert (second / str(cfg.server_uid) / "archive" / "2026" / "11" / "c.txt.zip").read_bytes() == b"three"
    assert (mirror / "a.txt.zip").read_bytes() == b"one, longer"
    assert (mirror / "b.txt.zip").stat().st_mtime_ns == marker


def test_the_mirror_never_deletes(tmp_path: Path) -> None:
    cfg, second = with_copy(tmp_path, archive=True)
    gone = put_archive(cfg, "2026/10/a.txt.zip", b"one")
    backup.mirror_archive(cfg)
    gone.unlink()
    backup.mirror_archive(cfg)
    assert (second / str(cfg.server_uid) / "archive" / "2026" / "10" / "a.txt.zip").is_file()


def test_the_mirror_is_off_without_copy_archive(tmp_path: Path) -> None:
    cfg, second = with_copy(tmp_path, archive=False)
    put_archive(cfg, "2026/10/a.txt.zip", b"one")
    assert backup.mirror_archive(cfg) is None
    assert not (second / str(cfg.server_uid) / "archive").exists()


def test_a_failing_mirror_stops_early_and_is_recorded(tmp_path: Path) -> None:
    cfg, second = with_copy(tmp_path, archive=True)
    (second / str(cfg.server_uid)).mkdir(parents=True)
    (second / str(cfg.server_uid) / "archive").write_text("blocks the folder", encoding="utf-8")
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
    backup.wait_for_copy()
    assert (second / str(cfg.server_uid) / "archive" / "2026" / "10" / "a.txt.zip").is_file()


def test_a_share_that_rounds_modification_times_does_not_get_everything_recopied(tmp_path: Path) -> None:
    cfg, second = with_copy(tmp_path, archive=True)
    src = put_archive(cfg, "2026/10/a.txt.zip", b"one")
    backup.mirror_archive(cfg)
    dest = second / str(cfg.server_uid) / "archive" / "2026" / "10" / "a.txt.zip"
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
    backup.wait_for_copy()
    backup.mirror_archive(cfg)
    assert [f.level for f in checks.backup_copy_check(cfg)] == [Level.OK, Level.OK]


def test_doctor_shows_a_failed_copy(tmp_path: Path) -> None:
    cfg, second = with_copy(tmp_path)
    second.write_text("broken", encoding="utf-8")
    backup.create_backup(cfg, now=lambda: T)
    backup.wait_for_copy()
    [finding] = checks.backup_copy_check(cfg)
    assert finding.level is Level.WARN
    assert str(second) in finding.title
    assert "could not copy" in finding.detail


# --- review 0.2.0 M6: the copy never stalls the writer, installs do not share a folder ---


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


def test_create_backup_does_not_wait_for_a_slow_second_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_the_daily_backup_does_not_wait_for_the_archive_mirror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


# --- review 0.2.0 L5 --------------------------------------------------------------------------------------------------


def test_cancel_copy_stops_a_running_mirror_between_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ctrl+C on a standalone `il2ks watch` must not wait hours for the first archive mirror: the stop handler cancels
    the copy, which ends at the next file (the next backup resumes: finished files are skipped)."""
    cfg, second = with_copy(tmp_path, archive=True)
    for n in range(20):
        put_archive(cfg, f"2026/10/m{n}.txt.zip", b"log")
    release = threading.Event()
    started = _slow_copy(monkeypatch, release)
    backup.request_copy(cfg, with_archive=True)
    assert started.wait(5)
    backup.cancel_copy()
    release.set()
    assert backup.wait_for_copy(5), "the copy thread kept running after cancel_copy"
    mirrored = list((second / str(cfg.server_uid) / "archive").rglob("*.zip"))
    assert len(mirrored) < 20


def test_cancel_copy_interrupts_a_single_big_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, second = with_copy(tmp_path, archive=True)
    put_archive(cfg, "2026/10/big.txt.zip", b"x" * 4000)
    monkeypatch.setattr(backup, "_CHUNK", 10)
    seen = threading.Event()
    real_cancelled = backup._copy_cancelled  # pyright: ignore[reportPrivateUsage]

    def spy() -> bool:
        seen.set()
        time.sleep(0.01)
        return real_cancelled()

    monkeypatch.setattr(backup, "_copy_cancelled", spy)
    backup.request_copy(cfg, with_archive=True)
    assert seen.wait(5)
    backup.cancel_copy()
    assert backup.wait_for_copy(5)
    mirror = second / str(cfg.server_uid) / "archive"
    assert not list(mirror.rglob("big*"))  # neither the file nor a .tmp is left


def test_the_standalone_watch_cancels_the_copy_when_it_ends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from il2ks.ingest import watch as watch_mod

    cancelled: list[bool] = []
    monkeypatch.setattr(watch_mod, "cancel_copy", lambda: cancelled.append(True))
    cfg, _ = with_copy(tmp_path)
    stop = threading.Event()
    stop.set()
    watch_mod.watch(cfg, lambda *a, **k: None, stop=stop)  # type: ignore[arg-type]
    assert cancelled


def test_status_writes_from_several_processes_do_not_lose_each_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The per-process `_status_lock` does not cover the watch thread plus `il2ks backup`: simulate two processes (no
    shared lock) hammering the status; both kinds must survive and the file must never be torn."""
    import contextlib

    cfg, _ = with_copy(tmp_path)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(backup, "_status_lock", contextlib.nullcontext())
    errors: list[BaseException] = []

    def hammer(kind: str) -> None:
        try:
            for n in range(150):
                backup._record(cfg, backup.CopyOutcome(kind, True, "t", f"{n}"))  # type: ignore[arg-type]  # pyright: ignore
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(k,)) for k in ("backups", "archive")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    status = backup.read_copy_status(cfg)
    assert status["backups"].detail == "149"
    assert status["archive"].detail == "149"
    assert not [p for p in cfg.data_dir.iterdir() if p.suffix == ".tmp"]


def test_two_copies_of_the_same_file_do_not_share_a_tmp_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`watch`'s mirror and the CLI's can copy the same file at once: each gets its own `.tmp`."""
    source = tmp_path / "src.zip"
    source.write_bytes(b"data")
    dest = tmp_path / "out" / "src.zip"
    used: list[str] = []
    arrived = threading.Semaphore(0)
    real_replace = os.replace

    def replace(a: object, b: object) -> None:
        first = Path(str(a)).name not in used
        used.append(Path(str(a)).name)
        if first and len(used) <= 2:  # both copies are at the final step together
            arrived.release()
            arrived.acquire(timeout=5)
            arrived.release()
        real_replace(a, b)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(backup.os, "replace", replace)
    threads = [threading.Thread(target=backup._copy_file, args=(source, dest)) for _ in range(2)]  # pyright: ignore
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(used)) == 2, used
    assert dest.read_bytes() == b"data"


def test_a_long_copy_keeps_its_tmp_fresh_so_nobody_deletes_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An SMB share may update the mtime only when the file is closed: the writer touches it while it copies, or another
    process (`_clean_partials`, one hour) would delete a long copy's .tmp."""
    source = tmp_path / "src.zip"
    source.write_bytes(b"x" * 100)
    monkeypatch.setattr(backup, "_CHUNK", 10)
    monkeypatch.setattr(backup, "_TOUCH_EVERY_S", 0.0)
    touched: list[str] = []
    real = os.utime

    def utime(path: object, *a: object, **k: object) -> None:
        touched.append(str(path))
        real(path, *a, **k)  # pyright: ignore

    monkeypatch.setattr(backup.os, "utime", utime)
    backup._copy_file(source, tmp_path / "out" / "src.zip")  # pyright: ignore[reportPrivateUsage]
    assert any(p.endswith(".tmp") for p in touched)
