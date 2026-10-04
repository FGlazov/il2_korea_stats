"""Pure helpers of the sortie pages: names, clocks, ammunition and mission labels (FR-WEB-6)."""

from datetime import UTC, datetime, timedelta

import pytest

from il2ks.db.models import GameObject, PlayerSortie
from il2ks.queries.sorties import DEFAULT_SORT, SortieFilters, parse_filters, resolve_sort
from il2ks.web.display import mission_name
from il2ks.web.flavor import Highlights
from il2ks.web.sortie_view import Lookup, build_highlights, damage_percent, hit_ammo, hit_damage, since, timeline_text


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


def test_the_timeline_end_names_the_outcome_and_the_displayed_fate() -> None:
    dead = PlayerSortie(is_death=True, is_captured=False, pilot_status="dead", pilot_fate="bailed_out")
    captured = PlayerSortie(is_death=False, is_captured=True, pilot_status="captured", pilot_fate="bailed_out")
    unknown = PlayerSortie(is_death=False, is_captured=False, pilot_status="healthy", pilot_fate="unknown")
    assert timeline_text(dead, "sortie_end", "shot_down") == "Shot down \N{MIDDLE DOT} Dead"
    assert timeline_text(captured, "sortie_end", "crashed") == "Crashed \N{MIDDLE DOT} Captured"
    assert timeline_text(unknown, "sortie_end", "landed") == "Landed \N{MIDDLE DOT} Survived"


def test_highlights_count_bomber_kills_and_time_to_the_first_air_kill() -> None:
    """Flavor facts from the timeline: victim classes come from the player victims' aircraft or the AI object."""
    start = datetime(2026, 9, 19, 20, 0, tzinfo=UTC)

    def entry(kind: str, seconds: int, detail: str = "", victim_sortie: int | None = None) -> dict[str, object]:
        counterpart = {"object_type": detail, "sortie_id": victim_sortie} if victim_sortie else {"object_type": detail}
        return {
            "kind": kind,
            "at": (start + timedelta(seconds=seconds)).isoformat(),
            "detail": detail,
            "counterpart": counterpart,
        }

    objects = {
        "Tu-2S": GameObject(log_name="Tu-2S", cls="bomber"),
        "Il-10": GameObject(log_name="Il-10", cls="attacker"),
        "MiG-15bis": GameObject(log_name="MiG-15bis", cls="fighter"),
        "M46 Patton": GameObject(log_name="M46 Patton", cls="vehicle"),
    }
    victim = PlayerSortie(aircraft=GameObject(log_name="B-26", cls="bomber"))
    sortie = PlayerSortie(
        spawned_at=start,
        took_off_at=start + timedelta(seconds=60),
        air_start=False,
        timeline=[
            entry("kill", 90, "M46 Patton"),  # ground kill: not an air kill
            entry("kill", 300, "MiG-15bis"),  # the first air kill: 240 s after takeoff
            entry("kill", 400, "Tu-2S"),
            entry("kill", 500, "Il-10"),
            entry("kill", 600, "B-26", victim_sortie=7),  # a player's bomber
            entry("assist", 700, "Tu-2S"),  # assists do not count
        ],
    )
    got = build_highlights(sortie, Lookup({7: victim}, objects))
    assert got == Highlights(bomber_kills=3, first_kill_s=240.0)
    assert build_highlights(PlayerSortie(spawned_at=start, timeline=[]), Lookup({}, {})) == Highlights(0, None)


@pytest.mark.parametrize(
    ("fraction", "expected"),
    [(0.0004, "0.04%"), (0.0035, "0.35%"), (0.01, "1.0%"), (0.125, "12.5%"), (0.4, "40.0%"), (1.0, "100.0%")],
)
def test_damage_percent_has_two_decimals_under_one_percent_and_one_above(fraction: float, expected: str) -> None:
    assert damage_percent(fraction) == expected


def test_hit_rows_show_the_sign_of_who_took_the_damage() -> None:
    assert hit_damage("hit_given", {"damage": 0.125}) == "+12.5%"
    assert hit_damage("hit_taken", {"damage": 0.125}) == "\N{MINUS SIGN}12.5%"


def test_rows_without_the_hit_fields_render_empty() -> None:
    """Timelines stored before the hit rows existed (and every non-hit row) have no damage or ammo."""
    assert hit_damage("kill", {}) == ""
    assert hit_damage("hit_given", {}) == ""  # a hit row whose damage is missing
    assert hit_damage("kill", {"damage": 0.5}) == ""  # only hit rows show a percentage
    assert hit_ammo({}) == ("", "")


def test_hit_ammo_is_the_plain_name_with_the_designation_for_a_tooltip() -> None:
    name, designation = hit_ammo({"ammo": "BULLET_12-7_USA_API"})
    assert name == ".50 BMG API"
    assert designation
    assert hit_ammo({"ammo": "M64", "ammo_kind": "ordnance"})[0].startswith("M64")
