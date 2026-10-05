"""Tour periods (TD-26, FR-WEB-10): which period contains an instant, in the tour timezone."""

from datetime import UTC, date, datetime, timedelta

import pytest

from il2ks.core.tours import MissionSpan, TourRules, parse_mode, period_for, segment_title, win_cuts

SEOUL = "Asia/Seoul"  # UTC+9, no DST


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("monthly", ("monthly", 0)),
        (" Monthly ", ("monthly", 0)),
        ("manual", ("manual", 0)),
        ("days:14", ("days", 14)),
        ("DAYS:1", ("days", 1)),
    ],
)
def test_parse_mode(text: str, expected: tuple[str, int]) -> None:
    assert parse_mode(text) == expected


@pytest.mark.parametrize("text", ["", "weekly", "days", "days:", "days:0", "days:-3", "days:x", "days:1.5"])
def test_parse_mode_rejects_anything_else(text: str) -> None:
    with pytest.raises(ValueError, match="monthly, manual or days"):
        parse_mode(text)


def test_parse_mode_bounds_the_tour_length() -> None:
    assert parse_mode("days:3660") == ("days", 3660)
    for text in ("days:3661", "days:999999999"):
        with pytest.raises(ValueError, match="3660"):
            parse_mode(text)


def test_label_round_trips_the_setting() -> None:
    assert TourRules().label == "monthly"
    assert TourRules(mode="days", days=14, start=date(2026, 1, 1)).label == "days:14"
    assert TourRules(mode="manual").label == "manual"


def test_monthly_period_in_utc() -> None:
    period = period_for(TourRules(), utc(2026, 10, 15, 12))
    assert (period.started_at, period.ended_at, period.title) == (utc(2026, 10, 1), utc(2026, 11, 1), "October 2026")


def test_monthly_december_rolls_into_the_next_year() -> None:
    period = period_for(TourRules(), utc(2026, 12, 31, 23, 59))
    assert (period.started_at, period.ended_at, period.title) == (utc(2026, 12, 1), utc(2027, 1, 1), "December 2026")


def test_a_mission_at_0030_local_on_the_first_belongs_to_the_new_month() -> None:
    """00:30 on 1 October in Seoul is 15:30 on 30 September in UTC: the tour timezone decides, not UTC."""
    rules = TourRules(timezone_name=SEOUL)
    just_after = period_for(rules, utc(2026, 9, 30, 15, 30))
    just_before = period_for(rules, utc(2026, 9, 30, 14, 30))  # 23:30 local on 30 September
    assert just_after.title == "October 2026"
    assert just_after.started_at == utc(2026, 9, 30, 15)  # local midnight in UTC
    assert just_before.title == "September 2026"
    assert just_before.ended_at == just_after.started_at  # contiguous


def test_boundary_instant_belongs_to_the_new_period() -> None:
    rules = TourRules(timezone_name=SEOUL)
    assert period_for(rules, utc(2026, 9, 30, 15)).title == "October 2026"
    assert period_for(rules, utc(2026, 9, 30, 14, 59, 59)).title == "September 2026"


def test_monthly_boundaries_follow_daylight_saving_time() -> None:
    """Berlin: 1 March 00:00 is CET (UTC+1), 1 April 00:00 is CEST (UTC+2): the March tour is 31 days minus 1 h."""
    rules = TourRules(timezone_name="Europe/Berlin")
    march = period_for(rules, utc(2026, 3, 15))
    assert march.started_at == utc(2026, 2, 28, 23)
    assert march.ended_at == utc(2026, 3, 31, 22)


def test_days_blocks_count_from_the_start_date() -> None:
    rules = TourRules(mode="days", days=14, start=date(2026, 10, 1), timezone_name="UTC")
    first = period_for(rules, utc(2026, 10, 1))
    last_of_first = period_for(rules, utc(2026, 10, 14, 23, 59))
    second = period_for(rules, utc(2026, 10, 15))
    assert (first.title, first.started_at, first.ended_at) == ("Tour 1", utc(2026, 10, 1), utc(2026, 10, 15))
    assert last_of_first == first
    assert (second.title, second.started_at) == ("Tour 2", utc(2026, 10, 15))


def test_days_boundary_is_local_midnight() -> None:
    rules = TourRules(mode="days", days=7, start=date(2026, 10, 1), timezone_name=SEOUL)
    assert period_for(rules, utc(2026, 10, 7, 14, 59)).title == "Tour 1"  # 23:59 on 7 October in Seoul
    assert period_for(rules, utc(2026, 10, 7, 15, 0)).title == "Tour 2"  # 00:00 on 8 October in Seoul


def test_days_blocks_before_the_start_date_count_backwards() -> None:
    rules = TourRules(mode="days", days=10, start=date(2026, 10, 1), timezone_name="UTC")
    before = period_for(rules, utc(2026, 9, 25))
    assert (before.title, before.started_at, before.ended_at) == ("Tour 0", utc(2026, 9, 21), utc(2026, 10, 1))


def test_manual_mode_has_no_calendar_periods() -> None:
    with pytest.raises(ValueError, match="manual"):
        period_for(TourRules(mode="manual"), utc(2026, 10, 1))


def test_days_mode_without_a_start_is_an_error() -> None:
    with pytest.raises(ValueError, match="start"):
        period_for(TourRules(mode="days", days=7), utc(2026, 10, 1))


# --- decisive missions start a new tour (maintainer request 2026-10-05, TD-26) ---


def span(start: datetime, minutes: int, *, decisive: bool) -> MissionSpan:
    return MissionSpan(start, start + timedelta(minutes=minutes), decisive)


def test_win_cuts_none_when_no_mission_is_decisive() -> None:
    spans = [span(utc(2026, 10, 1, 8), 170, decisive=False), span(utc(2026, 10, 1, 11), 170, decisive=False)]
    assert win_cuts(None, None, spans) == ()


def test_win_cuts_are_the_ends_of_the_decisive_missions_in_time_order() -> None:
    late = span(utc(2026, 10, 3, 8), 100, decisive=True)
    early = span(utc(2026, 10, 1, 8), 170, decisive=True)
    draw = span(utc(2026, 10, 2, 8), 170, decisive=False)
    assert win_cuts(None, None, [late, draw, early]) == (early.ended_at, late.ended_at)


def test_win_cuts_ignore_a_cut_outside_the_period_and_keep_equal_ones_once() -> None:
    inside = span(utc(2026, 10, 5, 8), 60, decisive=True)
    twin = span(utc(2026, 10, 5, 7), 120, decisive=True)  # ends when `inside` does
    spills_over = span(utc(2026, 10, 31, 23), 120, decisive=True)  # ends after the period
    before = span(utc(2026, 9, 30, 8), 60, decisive=True)  # ended before the period
    spills_in = span(utc(2026, 9, 30, 23, 30), 60, decisive=True)  # started before it, ends in it: the cut is there
    got = win_cuts(utc(2026, 10, 1), utc(2026, 11, 1), [inside, twin, spills_over, before, spills_in])
    assert got == (spills_in.ended_at, inside.ended_at)


def test_win_cuts_an_unbounded_period_takes_every_cut() -> None:
    first = span(utc(2020, 1, 1), 60, decisive=True)
    assert win_cuts(None, None, [first]) == (first.ended_at,)


def test_segment_title_numbers_the_parts_after_the_first() -> None:
    assert segment_title("October 2026", 0) == "October 2026"
    assert segment_title("October 2026", 1) == "October 2026 (2)"
    assert segment_title("Tour 4", 2) == "Tour 4 (3)"
