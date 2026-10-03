"""Grouping log files into missions and reading them (FR-ING-1, FR-ING-13)."""

import zipfile
from pathlib import Path

import pytest

from il2ks.core.logparse.events import LogVersionEvent, MissionEndEvent
from il2ks.core.logparse.files import (
    MissionLog,
    group_mission_files,
    mission_uid_from_name,
    parse_mission,
    part_index,
    read_mission_lines,
)
from il2ks.core.logparse.parser import ParseStats

UID = "2026-09-19_22-34-13"
UID2 = "2026-09-20_01-34-13"


def name(uid: str, n: int, ext: str = ".txt") -> str:
    return f"missionReport({uid})[{n}]{ext}"


@pytest.mark.parametrize(
    ("file_name", "uid", "index"),
    [
        (name(UID, 0), UID, 0),
        (name(UID, 3), UID, 3),
        (name(UID, 12), UID, 12),
        (name(UID, 0, ".txt.zip"), UID, None),
        (name(UID, 0, ".TXT"), UID, 0),
        ("missionReport(2026-09-19_22-34-13).txt", None, None),  # no [N]
        ("missionReport(2026-09-19)[0].txt", None, None),
        (f"{name(UID, 0)}.weather.json", None, None),
        ("notes.txt", None, None),
    ],
)
def test_name_parsing(file_name: str, uid: str | None, index: int | None) -> None:
    assert mission_uid_from_name(file_name) == uid
    assert part_index(file_name) == index


def test_parts_are_grouped_and_sorted_numerically(tmp_path: Path) -> None:
    files = [tmp_path / name(UID, n) for n in (10, 2, 0, 1)] + [tmp_path / name(UID2, 0), tmp_path / "other.txt"]
    logs = group_mission_files(reversed(files))
    assert logs == [
        MissionLog(UID, "parts", tuple(tmp_path / name(UID, n) for n in (0, 1, 2, 10))),
        MissionLog(UID2, "parts", (tmp_path / name(UID2, 0),)),
    ]


def test_txt_as_archive(tmp_path: Path) -> None:
    files = [tmp_path / name(UID, 0), tmp_path / name(UID2, 0, ".txt.zip")]
    assert group_mission_files(files, txt_as="archive") == [
        MissionLog(UID, "archive", (tmp_path / name(UID, 0),)),
        MissionLog(UID2, "archive", (tmp_path / name(UID2, 0, ".txt.zip"),)),
    ]


def test_plain_txt_archive_preferred_over_zip(tmp_path: Path) -> None:
    files = [tmp_path / name(UID, 0, ".txt.zip"), tmp_path / name(UID, 0)]
    assert group_mission_files(files, txt_as="archive") == [MissionLog(UID, "archive", (tmp_path / name(UID, 0),))]


def test_raw_parts_win_over_archive(tmp_path: Path) -> None:
    files = [tmp_path / name(UID, 0, ".txt.zip"), tmp_path / name(UID, 0), tmp_path / name(UID, 1)]
    expected = [MissionLog(UID, "parts", (tmp_path / name(UID, 0), tmp_path / name(UID, 1)))]
    assert group_mission_files(files) == expected
    # A [1] part proves the [0].txt is a raw part too, even in archive mode.
    assert group_mission_files(files, txt_as="archive") == expected


def test_duplicate_paths_and_indexes(tmp_path: Path) -> None:
    a = tmp_path / "a" / name(UID, 0)
    b = tmp_path / "b" / name(UID, 0)
    assert group_mission_files([b, a, a]) == [MissionLog(UID, "parts", (a,))]


def write_zip(path: Path, members: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for member, text in members.items():
            z.writestr(member, text)
    return path


def test_read_parts_in_order_crlf_and_bom(tmp_path: Path) -> None:
    (tmp_path / name(UID, 0)).write_bytes(b"\xef\xbb\xbfT:0 AType:15 VER:18\r\nT:1 AType:7 \r\n")
    (tmp_path / name(UID, 1)).write_bytes(b"T:2 AType:15 VER:18\nT:3 AType:19 ")
    [log] = group_mission_files(tmp_path.iterdir())
    assert list(read_mission_lines(log)) == [
        "T:0 AType:15 VER:18",
        "T:1 AType:7 ",
        "T:2 AType:15 VER:18",
        "T:3 AType:19 ",
    ]


def test_read_zip_without_extracting(tmp_path: Path) -> None:
    write_zip(tmp_path / name(UID, 0, ".txt.zip"), {name(UID, 0): "T:0 AType:15 VER:18\r\nT:5 AType:7 \r\n"})
    [log] = group_mission_files(tmp_path.iterdir())
    assert log.kind == "archive"
    stats = ParseStats()
    assert list(parse_mission(log, stats)) == [LogVersionEvent(tick=0, version=18), MissionEndEvent(tick=5)]
    assert stats.lines_total == 2
    assert stats.log_version == 18
    assert list(tmp_path.iterdir()) == [tmp_path / name(UID, 0, ".txt.zip")]


def test_undecodable_bytes_dont_stop_reading(tmp_path: Path) -> None:
    (tmp_path / name(UID, 0)).write_bytes(b"T:0 AType:99 X:\xff\r\nT:1 AType:7\r\n")
    [log] = group_mission_files(tmp_path.iterdir())
    assert list(read_mission_lines(log)) == ["T:0 AType:99 X:\ufffd", "T:1 AType:7"]


@pytest.mark.parametrize("members", [{}, {"a.txt": "x", "b.txt": "y"}, {"readme.md": "x"}])
def test_zip_must_hold_exactly_one_txt(tmp_path: Path, members: dict[str, str]) -> None:
    log = MissionLog(UID, "archive", (write_zip(tmp_path / name(UID, 0, ".txt.zip"), members),))
    with pytest.raises(ValueError, match=r"exactly one \.txt"):
        list(read_mission_lines(log))
