"""Incremental reading of growing parts (FR-ING-12): offsets, partial lines, new parts, restarts."""

from datetime import UTC, datetime
from pathlib import Path

from il2ks.core.logparse.files import MissionLog, read_mission_lines
from il2ks.core.logparse.tail import PartTail
from tests.live_helpers import LIVE_UID, fixture_lines, part_path, split_into_parts, write_part

NOW = datetime(2026, 9, 19, 21, 5, tzinfo=UTC)


def test_reads_only_the_new_lines_each_time(tmp_path: Path) -> None:
    tail = PartTail()
    part = part_path(tmp_path, LIVE_UID, 0)
    part.write_bytes(b"one\r\ntwo\r\n")
    assert tail.read_new([part]) == ["one", "two"]
    assert tail.read_new([part]) == []
    with part.open("ab") as stream:
        stream.write(b"three\r\n")
    assert tail.read_new([part]) == ["three"]


def test_a_half_written_line_waits_for_its_newline(tmp_path: Path) -> None:
    tail = PartTail()
    part = part_path(tmp_path, LIVE_UID, 0)
    part.write_bytes(b"one\r\ntw")
    assert tail.read_new([part]) == ["one"]
    with part.open("ab") as stream:
        stream.write(b"o\r\nthree")
    assert tail.read_new([part]) == ["two"]
    with part.open("ab") as stream:
        stream.write(b"\r\n")
    assert tail.read_new([part]) == ["three"]


def test_a_new_part_closes_the_previous_one(tmp_path: Path) -> None:
    """The writer moved on, so an unfinished last line of the old part is complete now."""
    tail = PartTail()
    first, second = part_path(tmp_path, LIVE_UID, 0), part_path(tmp_path, LIVE_UID, 1)
    first.write_bytes(b"one\r\ntwo")
    assert tail.read_new([first]) == ["one"]
    second.write_bytes(b"\xef\xbb\xbfthree\r\n")
    assert tail.read_new([first, second]) == ["two", "three"]  # BOM dropped, nothing repeated
    assert tail.read_new([first, second]) == []


def test_a_byte_order_mark_only_counts_at_the_start_of_a_part(tmp_path: Path) -> None:
    tail = PartTail()
    part = part_path(tmp_path, LIVE_UID, 0)
    part.write_bytes(b"\xef\xbb\xbfone\r\n")
    assert tail.read_new([part]) == ["one"]
    with part.open("ab") as stream:
        stream.write("﻿two\r\n".encode())
    assert tail.read_new([part]) == ["﻿two"]


def test_a_shrunk_or_missing_part_means_start_over(tmp_path: Path) -> None:
    tail = PartTail()
    part = part_path(tmp_path, LIVE_UID, 0)
    part.write_bytes(b"one\r\ntwo\r\n")
    tail.read_new([part])
    part.write_bytes(b"x\r\n")
    assert tail.read_new([part]) is None

    fresh = PartTail()
    fresh.read_new([part])
    part.unlink()
    assert fresh.read_new([]) is None


def test_a_part_appearing_before_one_already_read_means_start_over(tmp_path: Path) -> None:
    tail = PartTail()
    zero, one, two = (part_path(tmp_path, LIVE_UID, i) for i in range(3))
    zero.write_bytes(b"a\r\n")
    two.write_bytes(b"c\r\n")
    assert tail.read_new([zero, two]) == ["a", "c"]
    one.write_bytes(b"b\r\n")
    assert tail.read_new([zero, one, two]) is None


def test_growing_a_real_mission_gives_exactly_the_lines_of_a_batch_read(tmp_path: Path) -> None:
    lines = fixture_lines()
    parts = split_into_parts(lines, 4)
    tail = PartTail()
    got: list[str] = []
    paths: list[Path] = []
    for index, part_lines in enumerate(parts):
        half = len(part_lines) // 2
        paths.append(write_part(tmp_path, LIVE_UID, index, part_lines[:half], NOW, newline_at_end=False))
        got += tail.read_new(paths) or []
        paths[-1] = write_part(tmp_path, LIVE_UID, index, part_lines, NOW)
        got += tail.read_new(paths) or []
    assert got == list(read_mission_lines(MissionLog(LIVE_UID, "parts", tuple(paths))))
    assert got == lines
