"""The Elo of an aircraft type (maintainer, 2026-10-05; doc 13 "Aircraft type Elo"): every player-versus-player air kill
between two air superiority sorties is a game its killer's type wins against its victim's type. Per tour from a clean
slate (stored on the type's unfiltered `all` and `air_superiority` tour rows), all time = the best tour with at least
`RatingRules.type_min_games` games, games summed (`AircraftStats`)."""

import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from django.core import management
from django.db.migrations.executor import MigrationExecutor
from django.http import HttpResponse
from django.test import Client
from django.urls import reverse

from il2ks.core.ratings.elo import DEFAULT_RULES, Game, RatingRules, compute_type_ratings
from il2ks.core.replay.result import CombatRole
from il2ks.db.models import AircraftRole, AircraftStats, Tour, TourAircraftStats
from il2ks.db.site import get_site_settings
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ops.migrate import migrate_if_needed
from il2ks.web import columns
from tests.factories import kill, meta, mission, save, sortie

pytestmark = pytest.mark.django_db

AIR: CombatRole = "air_superiority"
SEPTEMBER = datetime(2026, 9, 19, 20, 0, tzinfo=UTC)
OCTOBER = datetime(2026, 10, 5, 20, 0, tzinfo=UTC)
RULES = replace(DEFAULT_RULES, min_games=1)  # a tour needs 10 games to set a type's all-time rating
MIG = "MiG-15bis"
SABRE = "F-86A-5"


def duel(
    killer_wins: int = 1,
    *,
    at: datetime = SEPTEMBER,
    killer: str = MIG,
    victim: str = SABRE,
    killer_role: CombatRole = AIR,
    victim_role: CombatRole = AIR,
) -> None:
    """One mission of `killer_wins` pairs of pilots: each killer flies `killer` and shoots down a pilot of `victim`."""
    sorties = tuple(
        item
        for n in range(killer_wins)
        for item in (
            sortie(2 * n, 1 + n, aircraft_type=killer, coalition=1, combat_role=killer_role),
            sortie(2 * n + 1, 101 + n, aircraft_type=victim, coalition=2, combat_role=victim_role),
        )
    )
    kills = tuple(
        kill(100 * (n + 1), 2 * n, 2 * n + 1, killer_type=killer, victim_type=victim) for n in range(killer_wins)
    )
    save(mission(sorties, kills), meta(at.strftime("%Y-%m-%d_%H-%M-%S"), at), ratings=RULES)


def all_time(log_name: str) -> tuple[float, int]:
    row = AircraftStats.objects.get(aircraft__log_name=log_name)
    return row.elo, row.elo_games


def tour_row(log_name: str, tour: Tour, role: AircraftRole = AircraftRole.ALL) -> TourAircraftStats:
    return TourAircraftStats.objects.get(aircraft__log_name=log_name, tour=tour, role=role, mod_pattern="")


def stat_rows() -> list[tuple[str, int, str, float, int]]:
    rows = TourAircraftStats.objects.filter(mod_pattern="").values_list(
        "aircraft__log_name", "tour_id", "role", "elo", "elo_games"
    )
    return sorted((name, tour or 0, role, elo, games) for name, tour, role, elo, games in rows)


def test_the_pure_replay_rates_each_type_against_the_other() -> None:
    found = compute_type_ratings([Game(1, "jet", 2, "jet", 10, 20), Game(1, "jet", 2, "jet", 10, 20)], RULES)
    assert found[10].games == found[20].games == 2
    assert found[10].rating > 1500.0 > found[20].rating
    assert found[10].rating + found[20].rating == pytest.approx(3000.0)


def test_a_duel_between_air_superiority_sorties_rates_both_types() -> None:
    duel()

    mig, sabre = all_time(MIG), all_time(SABRE)
    assert mig == (1516.0, 1)  # an even match: the winner gains k / 2
    assert sabre == (1484.0, 1)
    tour = Tour.objects.get()
    assert (tour_row(MIG, tour).elo, tour_row(MIG, tour).elo_games) == mig
    assert (tour_row(MIG, tour, AircraftRole.AIR_SUPERIORITY).elo, tour_row(SABRE, tour).elo) == (1516.0, 1484.0)


def test_attack_sorties_and_a_type_against_itself_are_no_games() -> None:
    duel(killer_role="attack")  # the killer flew an attack sortie
    duel(victim_role="attack", at=OCTOBER)
    duel(killer=MIG, victim=MIG, at=datetime(2026, 10, 6, 20, 0, tzinfo=UTC))

    assert all_time(MIG) == (1500.0, 0)
    assert all_time(SABRE) == (1500.0, 0)


def test_the_attack_role_row_carries_no_rating() -> None:
    duel(victim_role="attack")
    save(
        mission((sortie(0, 3, aircraft_type=MIG, coalition=1, combat_role="attack"),)),
        meta("2026-09-19_23-00-00", SEPTEMBER),
        ratings=RULES,
    )
    duel(at=datetime(2026, 9, 20, 20, 0, tzinfo=UTC))
    tour = Tour.objects.get()

    assert (tour_row(MIG, tour, AircraftRole.ATTACK).elo, tour_row(MIG, tour, AircraftRole.ATTACK).elo_games) == (
        1500.0,
        0,
    )
    assert tour_row(MIG, tour).elo_games == 1  # the duel of the second air superiority mission only


def test_every_tour_starts_from_a_clean_slate() -> None:
    duel(3, at=SEPTEMBER)
    duel(1, killer=SABRE, victim=MIG, at=OCTOBER)  # October: the Sabre wins one
    september, october = Tour.objects.order_by("started_at")

    assert tour_row(MIG, september).elo > 1500.0
    assert (tour_row(SABRE, october).elo, tour_row(SABRE, october).elo_games) == (1516.0, 1)  # not September's 1500-
    assert (tour_row(MIG, october).elo, tour_row(MIG, october).elo_games) == (1484.0, 1)


def test_all_time_is_the_best_qualifying_tour_and_games_add_up() -> None:
    duel(10, at=SEPTEMBER)  # the MiG wins ten: it qualifies (10 games), the Sabre has lost ten
    duel(1, killer=SABRE, victim=MIG, at=OCTOBER)  # October: one game each, below the minimum of ten
    september, october = Tour.objects.order_by("started_at")
    sept_mig, sept_sabre = tour_row(MIG, september).elo, tour_row(SABRE, september).elo

    assert all_time(MIG) == (sept_mig, 11)  # not October's 1484: one game in a new tour is noise
    assert all_time(SABRE) == (sept_sabre, 11)  # not October's 1516
    assert tour_row(SABRE, october).elo == 1516.0 > sept_sabre
    all_time_role = TourAircraftStats.objects.get(
        aircraft__log_name=MIG, tour=None, role=AircraftRole.AIR_SUPERIORITY, mod_pattern=""
    )
    assert (all_time_role.elo, all_time_role.elo_games) == (sept_mig, 11)


def test_rebuild_equals_incremental_and_repairs_drift() -> None:
    duel(10, at=SEPTEMBER)
    duel(2, killer=SABRE, victim=MIG, at=OCTOBER)
    expected = (all_time(MIG), all_time(SABRE))
    expected_rows = stat_rows()

    AircraftStats.objects.update(elo=1.0, elo_games=77)
    TourAircraftStats.objects.filter(mod_pattern="").update(elo=2.0, elo_games=88)
    rebuild_aggregates(RULES)

    assert (all_time(MIG), all_time(SABRE)) == expected
    assert stat_rows() == expected_rows


def test_a_changed_start_applies_to_types_without_games() -> None:
    duel(victim_role="attack")
    rebuild_aggregates(RatingRules(start=1000.0))

    assert all_time(MIG) == (1000.0, 0)


def test_migrating_a_database_from_before_the_type_elo_rebuilds_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The columns are new (default 1500 / 0) while the pilots' tour Elo exists: the `aircraft_elo` backfill asks for
    the one level-2 rebuild, which fills the type Elo."""
    from tests.ops_helpers import make_instance, recording, returning

    duel(3)
    expected = (all_time(MIG), all_time(SABRE))
    assert expected[0][1] == 3
    AircraftStats.objects.update(elo=1500.0, elo_games=0)
    TourAircraftStats.objects.update(elo=1500.0, elo_games=0)
    monkeypatch.setattr(MigrationExecutor, "migration_plan", returning([("fake", False)]))
    monkeypatch.setattr(management, "call_command", recording([], "migrate"))

    migrate_if_needed(make_instance(tmp_path), "ingest", wait=None)

    assert (all_time(MIG), all_time(SABRE)) == expected
    assert "aircraft_elo" in get_site_settings().backfills_done


# --- the aircraft list: the six default columns and the type Elo as a sortable column -----------------------------
DEFAULT_SORTS = ["aircraft", "sorties", "elo", "kl", "survival", "ground_hour"]


def list_page(client: Client, **params: str) -> HttpResponse:
    response = client.get(reverse("web:aircraft-list"), {"tour": "all", **params})
    assert response.status_code == 200
    return response


def seed_list() -> None:
    """A MiG that beat a Sabre, and an Il-10 that only attacked (no rated duel)."""
    duel()
    save(
        mission((sortie(0, 9, aircraft_type="IL-10", combat_role="attack"),)),
        meta("2026-09-19_23-30-00", SEPTEMBER),
        ratings=RULES,
    )


def test_the_aircraft_list_has_six_default_columns_and_the_rest_are_extras(client: Client) -> None:
    seed_list()

    html = list_page(client).content.decode()
    assert re.findall(r'<th[^>]*><a href="[^"]*sort=-?([a-z_]+)', html) == DEFAULT_SORTS
    assert "Attack proficiency" in html
    assert len(re.findall(r"<th[ >]", html)) == 6  # the picker lists "Hits to destroy", the table does not show it
    every = ",".join(c.key for c in columns.AIRCRAFT_COLUMNS)
    extras = list_page(client, cols=every).content.decode()
    assert len(re.findall(r"<th[ >]", extras)) == 6 + len(columns.AIRCRAFT_COLUMNS)
    for column in columns.AIRCRAFT_COLUMNS:
        assert column.key not in DEFAULT_SORTS  # nothing twice


def test_the_aircraft_list_sorts_by_the_type_elo_unrated_types_last(client: Client) -> None:
    seed_list()

    def order(sort: str) -> list[str]:
        return [r.stats.aircraft.log_name for r in list_page(client, sort=sort).context["rows"]]

    assert order("-elo") == [MIG, SABRE, "IL-10"]  # 1516, 1484, no duel
    assert order("elo") == [SABRE, MIG, "IL-10"]  # the unrated type is last both ways
    html = list_page(client, sort="-elo").content.decode()
    assert ">1,516<" in html
    assert ">1,500<" not in html  # a type without a duel shows a dash, not the start rating
