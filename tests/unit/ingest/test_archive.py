"""Archive: concatenate, verify, then move/keep/delete the originals (FR-ING-8, FR-ING-10, TD-09)."""

import hashlib
import os
import zipfile
from pathlib import Path

import pytest

from il2ks.ingest import archive
from il2ks.ingest.archive import (
    ArchiveError,
    archive_matches,
    archive_path_for,
    dispose_originals,
    file_sha256,
    iter_source_bytes,
    verify_archive,
    write_archive,
)

UID = "2026-09-19_22-34-13"


def test_archive_path_uses_year_and_month(tmp_path: Path) -> None:
    assert archive_path_for(tmp_path, UID) == tmp_path / "2026" / "09" / f"missionReport({UID})[0].txt.zip"
    with pytest.raises(ArchiveError):
        archive_path_for(tmp_path, "garbage")


def test_round_trip_concatenates_parts_in_order(tmp_path: Path) -> None:
    parts = [tmp_path / f"p{i}.txt" for i in range(3)]
    for i, part in enumerate(parts):
        part.write_bytes(f"T:{i} AType:15\r\nT:{i} AType:5\r\n".encode())
    target = archive_path_for(tmp_path / "archive", UID)
    result = write_archive(parts, target, UID)

    expected = b"".join(p.read_bytes() for p in parts)
    assert b"".join(iter_source_bytes(target)) == expected
    assert result.path == target
    assert result.size == len(expected)
    assert result.content_sha256 == hashlib.sha256(expected).hexdigest()
    assert result.sha256 == file_sha256(target)
    with zipfile.ZipFile(target) as zf:
        assert zf.namelist() == [f"missionReport({UID})[0].txt"]
    assert not list(target.parent.glob("*.tmp"))
    assert archive_matches(target, result.sha256)


def test_a_part_without_trailing_line_break_does_not_merge_lines(tmp_path: Path) -> None:
    first, second = tmp_path / "a.txt", tmp_path / "b.txt"
    first.write_bytes(b"T:1 AType:5")
    second.write_bytes(b"T:2 AType:6\r\n")
    target = tmp_path / "out.zip"
    write_archive([first, second], target, UID)
    assert b"".join(iter_source_bytes(target)) == b"T:1 AType:5\r\nT:2 AType:6\r\n"


def test_a_whole_mission_zip_can_be_a_source(tmp_path: Path) -> None:
    old = tmp_path / "old.zip"
    with zipfile.ZipFile(old, "w") as zf:
        zf.writestr("whatever.txt", "T:0 AType:0\r\n")
    late = tmp_path / "late.txt"
    late.write_bytes(b"T:9 AType:6\r\n")
    target = tmp_path / "new.zip"
    write_archive([old, late], target, UID)
    assert b"".join(iter_source_bytes(target)) == b"T:0 AType:0\r\nT:9 AType:6\r\n"


def test_writing_over_an_existing_archive_replaces_it(tmp_path: Path) -> None:
    src = tmp_path / "a.txt"
    target = tmp_path / "out.zip"
    src.write_bytes(b"one\r\n")
    write_archive([src], target, UID)
    src.write_bytes(b"two\r\n")
    write_archive([src], target, UID)
    assert b"".join(iter_source_bytes(target)) == b"two\r\n"


def test_verification_failure_leaves_no_archive_and_keeps_an_old_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "a.txt"
    src.write_bytes(b"one\r\n")
    target = tmp_path / "out.zip"
    write_archive([src], target, UID)
    good = target.read_bytes()

    def broken(path: Path, content_sha256: str) -> None:
        raise ArchiveError("boom")

    monkeypatch.setattr(archive, "verify_archive", broken)
    src.write_bytes(b"two\r\n")
    with pytest.raises(ArchiveError):
        write_archive([src], target, UID)
    assert target.read_bytes() == good
    assert not list(tmp_path.glob("*.tmp"))


def test_verify_detects_corruption(tmp_path: Path) -> None:
    src = tmp_path / "a.txt"
    src.write_bytes(os.urandom(5000))  # incompressible, so the middle of the zip is file data
    target = tmp_path / "out.zip"
    result = write_archive([src], target, UID)
    verify_archive(target, result.content_sha256)
    with pytest.raises(ArchiveError):
        verify_archive(target, "0" * 64)
    data = bytearray(target.read_bytes())
    data[len(data) // 2] ^= 0xFF
    target.write_bytes(bytes(data))
    with pytest.raises((ArchiveError, zipfile.BadZipFile)):
        verify_archive(target, result.content_sha256)
    assert not archive_matches(target, result.sha256)


def test_no_sources_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ArchiveError):
        write_archive([], tmp_path / "out.zip", UID)


def make_files(folder: Path) -> list[Path]:
    folder.mkdir()
    files = [folder / f"p{i}.txt" for i in range(2)]
    for f in files:
        f.write_text("x")
    return files


def test_dispose_move_keep_delete(tmp_path: Path) -> None:
    files = make_files(tmp_path / "logs")
    assert dispose_originals(files, "keep", tmp_path / "moved").done == ()
    assert all(f.exists() for f in files)

    result = dispose_originals(files, "move", tmp_path / "moved")
    assert len(result.done) == 2
    assert not any(f.exists() for f in files)
    assert sorted(p.name for p in (tmp_path / "moved").iterdir()) == ["p0.txt", "p1.txt"]

    files = make_files(tmp_path / "logs2")
    dispose_originals(files, "delete", tmp_path / "moved")
    assert not any(f.exists() for f in files)


def test_dispose_move_replaces_a_leftover_and_tolerates_missing_files(tmp_path: Path) -> None:
    files = make_files(tmp_path / "logs")
    moved = tmp_path / "moved"
    moved.mkdir()
    (moved / "p0.txt").write_text("older leftover")
    files[1].unlink()
    result = dispose_originals(files, "move", moved)
    assert result.failed == ()
    assert (moved / "p0.txt").read_text() == "x"


def test_dispose_collects_errors_instead_of_raising(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = make_files(tmp_path / "logs")

    def locked(src: Path, dst: Path) -> None:
        raise PermissionError("file is open in another process")

    monkeypatch.setattr(archive.shutil, "move", locked)
    result = dispose_originals(files, "move", tmp_path / "moved")
    assert result.done == ()
    assert [f for f, _ in result.failed] == files
    assert all(f.exists() for f in files)
