"""Pure helpers of the sortie pages: names, clocks, ammunition and mission labels (FR-WEB-6)."""

from datetime import UTC, datetime, timedelta

import pytest

from il2ks.queries.sorties import DEFAULT_SORT, SortieFilters, parse_filters, resolve_sort
from il2ks.web.display import mission_name
from il2ks.web.sortie_view import ammo_name, since


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("BULLET_12-7_USA_API", "12.7 USA API"),
        ("SHELL_20_USA_HEI", "20 USA HEI"),
        ("NapalmBullet", "NapalmBullet"),
        ("BULLET_7-62_RUS", "7.62 RUS"),
        ("RKT_HVAR-5", "HVAR-5"),
    ],
)
def test_ammo_name_is_readable(raw: str, expected: str) -> None:
    assert ammo_name(raw) == expected


def test_since_counts_from_the_spawn() -> None:
    start = datetime(2026, 9, 19, 20, 0, tzinfo=UTC)
    assert since(start, start) == "+0:00"
    assert since(start, start + timedelta(seconds=754)) == "+12:34"
    assert since(start, start + timedelta(seconds=3723)) == "+1:02:03"
    assert since(start, start - timedelta(seconds=5)) == "+0:00"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Multiplayer/Dogfight\\Author\\The_Bridges_1951\\The_Bridges_1951.msnbin", "The Bridges 1951"),
        ("missions/korea_test", "korea test"),
        ("", "—"),
    ],
)
def test_mission_name_is_the_readable_file_name(raw: str, expected: str) -> None:
    assert mission_name(raw) == expected


def test_resolve_sort_only_accepts_whitelisted_fields() -> None:
    assert resolve_sort("kills_air") == "kills_air"
    assert resolve_sort("-flight_time") == "-flight_time"
    assert resolve_sort("") == DEFAULT_SORT
    assert resolve_sort("-password") == DEFAULT_SORT
    assert resolve_sort("account_uuid") == DEFAULT_SORT


def test_parse_filters_ignores_values_that_are_not_choices() -> None:
    raw = {"aircraft": "7", "outcome": "landed", "role": "pilot", "combat_role": "attack"}
    assert parse_filters(raw, {7, 8}) == SortieFilters(7, "landed", "pilot", "attack")
    assert parse_filters(raw, {8}).aircraft is None
    assert parse_filters({"aircraft": "x", "outcome": "nope", "role": "", "combat_role": "?"}, {7}) == SortieFilters()
