"""Mission start resolution from the local-time file name (TD-15), including both DST edges."""

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from il2ks.ingest.timeutil import parse_mission_uid, resolve_mission_start

BERLIN = ZoneInfo("Europe/Berlin")
LISBON = ZoneInfo("Europe/Lisbon")


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_utc_zone_is_identity() -> None:
    r = resolve_mission_start("2026-09-19_22-34-13", ZoneInfo("UTC"))
    assert r.started_at == utc(2026, 9, 19, 22, 34, 13)
    assert r.started_at.tzinfo is UTC
    assert r.warnings == ()


def test_summer_time_offset() -> None:
    r = resolve_mission_start("2026-09-19_22-34-13", BERLIN)
    assert r.started_at == utc(2026, 9, 19, 20, 34, 13)
    assert r.warnings == ()


def test_winter_time_offset() -> None:
    assert resolve_mission_start("2026-01-10_12-00-00", LISBON).started_at == utc(2026, 1, 10, 12, 0, 0)


def test_result_is_utc_aware() -> None:
    r = resolve_mission_start("2026-09-19_22-34-13", BERLIN)
    assert r.started_at.utcoffset() == timedelta(0)


# Berlin 2026: clocks go back 03:00 CEST -> 02:00 CET on 25 October, so 02:30 happens twice (00:30Z and 01:30Z).
def test_fall_back_without_hint_takes_earlier_instant_and_warns() -> None:
    r = resolve_mission_start("2026-10-25_02-30-00", BERLIN)
    assert r.started_at == utc(2026, 10, 25, 0, 30, 0)
    assert len(r.warnings) == 1
    assert "ambiguous" in r.warnings[0]


def test_fall_back_hint_picks_closest_candidate() -> None:
    first = resolve_mission_start("2026-10-25_02-30-00", BERLIN, hint_utc=utc(2026, 10, 25, 0, 30, 20))
    second = resolve_mission_start("2026-10-25_02-30-00", BERLIN, hint_utc=utc(2026, 10, 25, 1, 30, 20))
    assert first.started_at == utc(2026, 10, 25, 0, 30, 0)
    assert second.started_at == utc(2026, 10, 25, 1, 30, 0)
    assert first.warnings == second.warnings == ()


def test_fall_back_hint_in_another_offset_and_naive_hint() -> None:
    plus2 = timezone(timedelta(hours=2))
    aware = resolve_mission_start("2026-10-25_02-30-00", BERLIN, hint_utc=datetime(2026, 10, 25, 3, 31, tzinfo=plus2))
    naive = resolve_mission_start("2026-10-25_02-30-00", BERLIN, hint_utc=datetime(2026, 10, 25, 1, 31))
    assert aware.started_at == naive.started_at == utc(2026, 10, 25, 1, 30, 0)


def test_hint_ignored_when_not_ambiguous() -> None:
    r = resolve_mission_start("2026-09-19_22-34-13", BERLIN, hint_utc=utc(2020, 1, 1))
    assert r.started_at == utc(2026, 9, 19, 20, 34, 13)
    assert r.warnings == ()


# Lisbon 2026: clocks go forward 01:00 WET -> 02:00 WEST on 29 March, so 01:30 never happens.
def test_spring_forward_gap_shifts_forward_and_warns() -> None:
    r = resolve_mission_start("2026-03-29_01-30-00", LISBON)
    assert r.started_at == utc(2026, 3, 29, 1, 30, 0)  # = 02:30 WEST, the wall time moved forward by 1 h
    assert len(r.warnings) == 1
    assert "DST gap" in r.warnings[0]


def test_spring_forward_gap_berlin() -> None:
    r = resolve_mission_start("2026-03-29_02-15-00", BERLIN)
    assert r.started_at == utc(2026, 3, 29, 1, 15, 0)  # = 03:15 CEST
    assert r.warnings


def test_times_next_to_transitions_are_unambiguous() -> None:
    assert resolve_mission_start("2026-10-25_01-59-59", BERLIN).warnings == ()
    assert resolve_mission_start("2026-10-25_03-00-00", BERLIN).started_at == utc(2026, 10, 25, 2, 0, 0)
    assert resolve_mission_start("2026-03-29_03-00-00", BERLIN).started_at == utc(2026, 3, 29, 1, 0, 0)


@pytest.mark.parametrize(
    "uid",
    ["", "2026-09-19", "2026-09-19 22-34-13", "2026-09-19_22-34-13[0]", "2026-13-01_00-00-00", "2026-02-30_00-00-00"],
)
def test_malformed_uid_raises(uid: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        resolve_mission_start(uid, BERLIN)


def test_parse_mission_uid() -> None:
    assert parse_mission_uid("2026-09-19_22-34-13") == datetime(2026, 9, 19, 22, 34, 13)
