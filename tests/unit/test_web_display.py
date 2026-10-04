"""Display helpers behind the template tags (TD-22: ratios and durations are computed at read time)."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from il2ks.web import display, icons


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0 s"),
        (45, "45 s"),
        (59.6, "1 min"),
        (720, "12 min"),
        (750, "12 min"),
        (4980, "1 h 23 min"),
        (7200, "2 h"),
        (Decimal("90"), "1 min"),
        (None, display.DASH),
        (-5, display.DASH),
        ("abc", display.DASH),
    ],
)
def test_duration(seconds: object, expected: str) -> None:
    assert display.duration(seconds) == expected


def test_utc_converts_aware_datetimes_and_labels_the_zone() -> None:
    tokyo = timezone(timedelta(hours=9))
    assert display.utc(datetime(2026, 9, 20, 7, 34, tzinfo=tokyo)) == "2026-09-19 22:34 UTC"
    assert display.utc(datetime(2026, 9, 19, 22, 34, tzinfo=UTC)) == "2026-09-19 22:34 UTC"
    assert display.utc(datetime(2026, 9, 19, 22, 34)) == "2026-09-19 22:34 UTC"
    assert display.utc(None) == display.DASH


def test_num_groups_thousands_and_rounds() -> None:
    assert display.num(1234567) == "1,234,567"
    assert display.num(1234.5678, 1) == "1,234.6"
    assert display.num("12") == "12"
    assert display.num(None) == display.DASH


def test_ratio_divides_at_read_time_and_survives_zero() -> None:
    assert display.ratio(482, 205) == "2.35"
    assert display.ratio(7, 0) == display.DASH
    assert display.ratio(0, 5) == "0.00"
    assert display.ratio(None, 5) == display.DASH
    assert display.ratio(5, "x") == display.DASH


def test_per_hour_and_percent() -> None:
    assert display.per_hour(12, 18000) == "2.40"
    assert display.per_hour(12, 0) == display.DASH
    assert display.percent(87, 100) == "87%"
    assert display.percent(1, 3, 1) == "33.3%"
    assert display.percent(1, 0) == display.DASH


@pytest.mark.parametrize(
    ("value", "side"),
    [
        (501, "redfor"),
        (503, "redfor"),
        (601, "blufor"),
        ("602", "blufor"),
        ("redfor", "redfor"),
        (0, None),
        (999, None),
    ],
)
def test_side_of(value: object, side: str | None) -> None:
    assert display.side_of(value) == side


def test_side_name_uses_the_admin_editable_names() -> None:
    assert display.side_name(501, "Reds", "Blues") == "Reds"
    assert display.side_name(601, "Reds", "Blues") == "Blues"
    assert display.side_name(0, "Reds", "Blues") == "Neutral"


def test_every_model_enum_value_has_a_badge() -> None:
    from il2ks.db.models import AircraftStatus, CombatRole, Outcome, PilotFate, PilotStatus

    for table, choices in [
        (display.OUTCOMES, Outcome),
        (display.FATES, PilotFate),
        (display.STATUSES, PilotStatus),
        (display.AIRCRAFT_STATUSES, AircraftStatus),
        (display.ROLES, CombatRole),
    ]:
        assert set(table) == {choice.value for choice in choices}


def test_pilot_fate_disconnected_reads_left_the_server() -> None:
    """The flag is also set after landings and bailouts, so the label must not say 'disconnected in flight'."""
    label, _, _ = display.badge_spec(display.FATES, "disconnected")
    assert label == "Left the server"


def test_badge_spec_degrades_for_unknown_values() -> None:
    assert display.badge_spec(display.OUTCOMES, "shot_down")[:2] == ("Shot down", "red")
    assert display.badge_spec(display.OUTCOMES, "brand_new") == ("Brand new", "grey", "")
    assert display.badge_spec(display.OUTCOMES, "") == (display.DASH, "grey", "")
    assert display.badge_spec(display.OUTCOMES, None) == (display.DASH, "grey", "")


def test_accent_css_accepts_only_rrggbb() -> None:
    assert display.accent_css("#A86A14") == ":root{--il2-accent:#a86a14;--il2-accent-contrast:#ffffff}"
    assert display.accent_css("#ffd400").endswith("--il2-accent-contrast:#10161c}")  # light accent gets dark text
    for bad in [
        "",
        "red",
        "#fff",
        "#12345",
        "#1234567",
        "#12345g",
        "javascript:alert(1)",
        "#aabbcc}body{x:y",
        " #aabbcc",
    ]:
        assert display.accent_css(bad) == ""


def test_next_sort_toggles_and_numeric_columns_start_descending() -> None:
    assert display.next_sort("", "kills", "desc") == "-kills"
    assert display.next_sort("-kills", "kills", "desc") == "kills"
    assert display.next_sort("kills", "kills") == "-kills"
    assert display.next_sort("deaths", "name") == "name"
    assert display.next_sort("-deaths", "name") == "name"


def test_page_links_window() -> None:
    def numbers(current: int, last: int) -> list[int | None]:
        return [link.number for link in display.page_links(current, last)]

    assert numbers(1, 1) == [1]
    assert numbers(1, 5) == [1, 2, 3, 4, 5]
    assert numbers(1, 20) == [1, 2, 3, None, 20]
    assert numbers(10, 20) == [1, None, 8, 9, 10, 11, 12, None, 20]
    assert numbers(20, 20) == [1, None, 18, 19, 20]
    assert [link.current for link in display.page_links(2, 3)] == [False, True, False]


def test_aircraft_icon_name_falls_back_by_propulsion() -> None:
    assert icons.slug("MiG-15bis") == "mig-15bis"
    assert icons.slug("F-86F-30 Sabre") == "f-86f-30-sabre"
    assert icons.aircraft_icon_name("Not-A-Real-Type", "jet") == "aircraft/generic-jet"
    assert icons.aircraft_icon_name("Not-A-Real-Type", "prop") == "aircraft/generic-prop"
    assert icons.aircraft_icon_name("Not-A-Real-Type", "") == "aircraft/unknown"
    assert icons.aircraft_icon_name("", "") == "aircraft/unknown"
