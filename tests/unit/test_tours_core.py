"""Tour periods (TD-26, FR-WEB-10): which period contains an instant, in the tour timezone."""

from datetime import UTC, date, datetime

import pytest

from il2ks.core.tours import TourRules, parse_mode, period_for

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
