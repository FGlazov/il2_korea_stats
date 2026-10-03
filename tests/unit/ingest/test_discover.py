"""Discovery: completeness (FR-ING-2, FR-ING-16), fingerprint (FR-ING-18), history classification (FR-ING-19)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from il2ks.config import IngestConfig
from il2ks.core.logparse.files import MissionLogKind
from il2ks.ingest.discover import (
    FileState,
    LastRun,
    classify,
    discover,
    fingerprint,
    list_log_files,
    next_retry,
)
from tests.ingest_fakes import T0, fake_group, part_name, set_mtime, write_parts, write_zip

CFG = IngestConfig(idle_minutes=10, settle_seconds=60, stable_seconds=60)
NOW = T0.timestamp()
A, B = "2026-09-19_10-00-00", "2026-09-19_13-00-00"


def run_discover(
    folder: Path, *, remote: bool = False, all_complete: bool = False, txt_as: MissionLogKind = "parts"
) -> dict[str, str | None]:
    found = discover(
        list_log_files(folder),
        lambda paths: fake_group(paths, txt_as),
        now=NOW,
        cfg=CFG,
        remote=remote,
        treat_all_complete=all_complete,
    )
    return {f.mission_uid: f.complete for f in found}


def test_mission_end_marker_completes_after_settle(tmp_path: Path) -> None:
    write_parts(tmp_path, A, ["x", "y"], age_s=120, end=True)
    assert run_discover(tmp_path) == {A: "mission_end"}


def test_mission_end_marker_waits_for_cleanup_lines(tmp_path: Path) -> None:
    write_parts(tmp_path, A, ["x", "y"], age_s=10, end=True)
    assert run_discover(tmp_path) == {A: None}


def test_mission_end_marker_is_looked_for_in_the_last_parts_only(tmp_path: Path) -> None:
    paths = write_parts(tmp_path, A, ["a", "b", "c", "d", "e"], age_s=120, end=False)
    paths[0].write_bytes(b"T:5 AType:7\r\n")
    set_mtime(paths[0], T0 - timedelta(seconds=120))
    assert run_discover(tmp_path) == {A: None}


def test_a_line_starting_with_atype_7x_is_not_the_end(tmp_path: Path) -> None:
    paths = write_parts(tmp_path, A, ["x"], age_s=120, end=False)
    paths[0].write_bytes(b"T:5 AType:70 FOO:1\r\n")
    set_mtime(paths[0], T0 - timedelta(seconds=120))
    assert run_discover(tmp_path) == {A: None}


def test_idle_timeout_completes_a_mission_without_end_marker(tmp_path: Path) -> None:
    write_parts(tmp_path, A, ["x"], age_s=11 * 60, end=False)
    assert run_discover(tmp_path) == {A: "idle"}


def test_running_mission_is_incomplete(tmp_path: Path) -> None:
    write_parts(tmp_path, A, ["x"], age_s=30, end=False)
    assert run_discover(tmp_path) == {A: None}


def test_newer_mission_first_part_completes_the_older_one(tmp_path: Path) -> None:
    write_parts(tmp_path, A, ["x"], age_s=30, end=False)
    write_parts(tmp_path, B, ["x"], age_s=5, end=False)
    assert run_discover(tmp_path) == {A: "newer_mission", B: None}


def test_newer_mission_without_first_part_does_not_count(tmp_path: Path) -> None:
    write_parts(tmp_path, A, ["x"], age_s=30, end=False)
    (tmp_path / part_name(B, 4)).write_bytes(b"T:1 AType:15\r\n")
    assert run_discover(tmp_path)[A] is None


def test_remote_mode_waits_for_stable_size_even_with_a_newer_mission(tmp_path: Path) -> None:
    write_parts(tmp_path, A, ["x", "y"], age_s=30, end=False)
    write_parts(tmp_path, B, ["x"], age_s=5, end=False)
    assert run_discover(tmp_path, remote=True)[A] is None
    for path in tmp_path.glob(f"*{A}*"):
        set_mtime(path, T0 - timedelta(seconds=90))
    assert run_discover(tmp_path, remote=True)[A] == "newer_mission"


def test_whole_mission_archives_are_complete(tmp_path: Path) -> None:
    write_zip(tmp_path / f"{part_name(A, 0)}.zip", A, "T:0 AType:0\r\n")
    (tmp_path / part_name(B, 0)).write_bytes(b"T:0 AType:0\r\n")
    for path in tmp_path.iterdir():
        set_mtime(path, T0 - timedelta(seconds=5))
    assert run_discover(tmp_path, txt_as="archive") == {A: "import", B: "import"}
    assert run_discover(tmp_path, txt_as="parts") == {A: "import", B: None}  # a raw `[0].txt` is a running mission


def test_remote_mode_waits_for_a_whole_mission_archive_to_be_fully_copied(tmp_path: Path) -> None:
    zip_path = write_zip(tmp_path / f"{part_name(A, 0)}.zip", A, "T:0 AType:0\r\n")
    set_mtime(zip_path, T0 - timedelta(seconds=5))
    assert run_discover(tmp_path, remote=True) == {A: None}
    set_mtime(zip_path, T0 - timedelta(seconds=90))
    assert run_discover(tmp_path, remote=True) == {A: "import"}


def test_import_treats_everything_as_complete(tmp_path: Path) -> None:
    write_parts(tmp_path, A, ["x"], age_s=1, end=False)
    assert run_discover(tmp_path, all_complete=True) == {A: "import"}


def test_files_of_other_names_are_ignored(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("hi")
    (tmp_path / "sub").mkdir()
    assert run_discover(tmp_path) == {}


def test_fingerprint_changes_with_a_part_size_or_mtime(tmp_path: Path) -> None:
    paths = write_parts(tmp_path, A, ["x", "y"], age_s=100)

    def fp() -> str:
        return fingerprint([FileState.of(p) for p in paths])

    before = fp()
    assert fp() == before
    set_mtime(paths[0], T0 - timedelta(seconds=50))
    after_mtime = fp()
    assert after_mtime != before
    paths[1].write_bytes(paths[1].read_bytes() + b"more")
    assert fp() != after_mtime


def test_fingerprint_ignores_the_folder(tmp_path: Path) -> None:
    one = write_parts(tmp_path / "one", A, ["x"], age_s=100)
    two = write_parts(tmp_path / "two", A, ["x"], age_s=100)
    assert fingerprint([FileState.of(p) for p in one]) == fingerprint([FileState.of(p) for p in two])


type Status = Literal["ok", "failed", "skipped"]


def last(
    status: Status, *, fp: str = "f", attempts: int = 1, retry: datetime | None = None, version: str = "1"
) -> LastRun:
    return LastRun(status, fp, attempts, retry, version)


def test_classify_new_unchanged_changed() -> None:
    assert classify("f", None, version="1", now=T0) == "new"
    assert classify("f", last("ok"), version="1", now=T0) == "unchanged"
    assert classify("g", last("ok"), version="1", now=T0) == "changed"


def test_classify_failed_respects_backoff() -> None:
    soon = T0 + timedelta(minutes=5)
    assert classify("f", last("failed", retry=soon), version="1", now=T0) == "backoff"
    assert classify("f", last("failed", retry=T0), version="1", now=T0) == "retry"
    assert classify("f", last("failed", retry=None), version="1", now=T0) == "gave_up"


def test_classify_failed_retries_at_once_on_new_files_or_new_version() -> None:
    soon = T0 + timedelta(hours=1)
    assert classify("g", last("failed", retry=soon), version="1", now=T0) == "retry"
    assert classify("f", last("failed", retry=soon), version="2", now=T0) == "retry"
    assert classify("f", last("failed", retry=None), version="2", now=T0) == "retry"


def test_next_retry_follows_the_schedule_then_stops() -> None:
    backoff = (timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2))
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert next_retry(1, backoff, now) == now + timedelta(minutes=5)
    assert next_retry(2, backoff, now) == now + timedelta(minutes=30)
    assert next_retry(3, backoff, now) == now + timedelta(hours=2)
    assert next_retry(4, backoff, now) is None
