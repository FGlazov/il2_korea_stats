"""Aircraft stats by combat role (FR-WEB-8) and the loadout effectiveness table: level-2 rows per tour and role
(incremental == rebuild), the average pilot Elo of a loadout, the `?role=` toggle on the pages, hidden rules (FR-ADM-3)
and the simple-reads budgets (TD-22)."""

from datetime import timedelta

import pytest
from django.db import models
from django.test import Client, override_settings
from django.urls import reverse

from il2ks.config import LeaderboardConfig
from il2ks.db.models import (
    AircraftPayload,
    AircraftStats,
    GameObject,
    Player,
    PlayerAircraft,
    Tour,
    TourAircraftStats,
)
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.queries.aircraft import Loadout
from tests.factories import STARTED_AT, kill, meta, mission, save, sortie
from tests.simple_reads import assert_simple_reads

pytestmark = pytest.mark.django_db

AIR = "air_superiority"
ATTACK = "attack"
OCTOBER = STARTED_AT + timedelta(days=40)
SOME = LeaderboardConfig(
    min_sorties=1,
    min_elo_games=1,
    min_attack_sorties=1,
    min_time_on_target_minutes=1.0,
    min_air_superiority_sorties=1,
    min_air_superiority_minutes=1.0,
)


def snapshot() -> dict[str, list[dict[str, object]]]:
    def rows(model: type[models.Model], *order: str) -> list[dict[str, object]]:
        found = list(model.objects.order_by(*order).values())
        for row in found:
            row.pop("id")
        return found

    return {
        "stats": rows(AircraftStats, "aircraft_id"),
        "tour_stats": rows(TourAircraftStats, "aircraft_id", "tour_id", "role"),
        "payloads": rows(AircraftPayload, "aircraft_id", "payload_name", "combat_role"),
    }


def history() -> tuple[Tour, Tour]:
    """September: MiG pilots 1 (an air sortie, then an attack one) and 2 (attack), a Sabre (4) that player 1 shot down
    and one that shot player 2 down. October: MiG pilot 1 (air) shot down by Sabre pilot 4."""
    save(
        mission(
            (
                sortie(0, 1, kills_air=2, kills_air_pvp=2, payload_id=1, combat_role=AIR),
                sortie(
                    1,
                    1,
                    payload_id=2,
                    combat_role=ATTACK,
                    ground_by_category={"tank": 3},
                    time_on_target_s=600.0,
                ),
                sortie(
                    2,
                    2,
                    payload_id=2,
                    combat_role=ATTACK,
                    ground_by_category={"tank": 1},
                    time_on_target_s=300.0,
                    is_death=True,
                    is_plane_lost=True,
                ),
                sortie(3, 4, aircraft_type="F-86A-5", coalition=2, payload_id=1, combat_role=AIR, is_death=True),
                sortie(4, 5, aircraft_type="F-86A-5", coalition=2, payload_id=1, combat_role=AIR),
            ),
            (
                kill(100, 0, 3, victim_type="F-86A-5"),
                kill(200, 4, 2, killer_type="F-86A-5", victim_type="MiG-15bis"),
            ),
        )
    )
    save(
        mission(
            (
                sortie(0, 1, payload_id=1, combat_role=AIR, is_death=True, is_plane_lost=True),
                sortie(
                    1,
                    4,
                    aircraft_type="F-86A-5",
                    coalition=2,
                    payload_id=1,
                    combat_role=AIR,
                    kills_air=1,
                    kills_air_pvp=1,
                ),
            ),
            (kill(100, 1, 0, killer_type="F-86A-5", victim_type="MiG-15bis"),),
        ),
        meta("2026-10-29_22-00-00", OCTOBER),
    )
    september, october = Tour.objects.order_by("started_at")
    return september, october


def role_row(log_name: str, tour: Tour | None, role: str) -> TourAircraftStats:
    return TourAircraftStats.objects.get(aircraft__log_name=log_name, tour=tour, role=role)


def test_role_rows_per_tour_and_all_time_with_distinct_pilots() -> None:
    september, october = history()

    air = role_row("MiG-15bis", september, AIR)
    attack = role_row("MiG-15bis", september, ATTACK)
    assert (air.sorties, air.pilots, air.kills_air, air.side) == (1, 1, 2, "redfor")
    assert (attack.sorties, attack.pilots, attack.kills_ground, attack.attack_sorties) == (2, 2, 4, 2)
    assert attack.time_on_target_s == 900.0
    assert role_row("MiG-15bis", october, AIR).sorties == 1
    assert not TourAircraftStats.objects.filter(aircraft__log_name="MiG-15bis", tour=october, role=ATTACK).exists()
    # all time: a null tour; a pilot who flew in two tours is ONE pilot of the type (a sum of tour rows would say 2)
    all_air = role_row("MiG-15bis", None, AIR)
    assert (all_air.sorties, all_air.pilots) == (2, 1)
    assert role_row("MiG-15bis", None, ATTACK).pilots == 2
    # the `all` role stays what the per-tour player rows sum to; the all-time `all` row is AircraftStats alone
    assert role_row("MiG-15bis", september, "all").sorties == air.sorties + attack.sorties
    assert not TourAircraftStats.objects.filter(tour__isnull=True, role="all").exists()
    assert AircraftStats.objects.get(aircraft__log_name="MiG-15bis").sorties == 4


def test_incremental_equals_rebuild_with_a_reingest_into_another_tour() -> None:
    history()
    # Re-ingest the first mission without its attack sorties: their role rows and loadout must go away again.
    save(
        mission(
            (
                sortie(0, 1, kills_air=2, kills_air_pvp=2, payload_id=1, combat_role=AIR),
                sortie(1, 4, aircraft_type="F-86A-5", coalition=2, payload_id=1, combat_role=AIR, is_death=True),
            ),
            (kill(100, 0, 1, victim_type="F-86A-5"),),
        )
    )
    incremental = snapshot()

    rebuild_aggregates()

    assert snapshot() == incremental
    assert not TourAircraftStats.objects.filter(role=ATTACK).exists()
    assert not AircraftPayload.objects.filter(combat_role=ATTACK).exists()


def test_rebuild_repairs_drifted_role_rows_and_loadout_elo() -> None:
    history()
    good = snapshot()
    TourAircraftStats.objects.filter(role=AIR).update(sorties=99)
    TourAircraftStats.objects.filter(role=ATTACK).delete()
    AircraftPayload.objects.update(elo_avg=1234.5, time_on_target_s=1.0)

    rebuild_aggregates()

    assert snapshot() == good


def test_loadout_average_elo_is_sortie_weighted_with_type_then_pool_elo() -> None:
    history()
    mig = GameObject.objects.get(log_name="MiG-15bis")
    sabre = GameObject.objects.get(log_name="F-86A-5")
    # Sabre loadout 1: sorties by players 4 (two: one per tour) and 5 (one). Player 4 has rated games in the type.
    elo = {
        (p.player.account_uuid[-1], p.aircraft_id): p.elo
        for p in PlayerAircraft.objects.select_related("player")
        if p.elo_games > 0
    }
    weighted = 0.0
    sorties_rated = 0
    for player, sorties in (("4", 2), ("5", 1)):
        value = elo.get((player, sabre.pk))
        if value is None:  # no games in the type: the pool's rating if there is one
            row = Player.objects.get(account_uuid__endswith=f"00000000000{player}")
            value = row.elo_jet if row.elo_jet_games else row.elo_prop if row.elo_prop_games else None
        if value is not None:
            weighted += value * sorties
            sorties_rated += sorties
    assert sorties_rated > 0
    stored = AircraftPayload.objects.get(aircraft=sabre, payload_name="Payload 1", combat_role=AIR)
    assert stored.elo_avg == pytest.approx(weighted / sorties_rated, abs=0.001)
    # attack loadouts have no Elo; neither has the other type's attack loadout
    assert AircraftPayload.objects.get(aircraft=mig, payload_name="Payload 2", combat_role=ATTACK).elo_avg is None


def test_loadout_without_a_rated_pilot_has_no_average_elo() -> None:
    save(mission((sortie(0, 1, payload_id=1, combat_role=AIR),), ()))

    assert AircraftPayload.objects.get().elo_avg is None


def detail(log_name: str) -> str:
    return reverse("web:aircraft-detail", args=[GameObject.objects.get(log_name=log_name).pk])


def test_detail_role_toggle_filters_tiles_pilots_and_loadouts_per_tour(client: Client) -> None:
    september, october = history()
    url = detail("MiG-15bis")

    everything = client.get(f"{url}?tour={september.pk}")
    air = client.get(f"{url}?tour={september.pk}&role=air_superiority")
    attack = client.get(f"{url}?tour={september.pk}&role=attack")
    all_time_attack = client.get(f"{url}?tour=all&role=attack")

    assert [r.context["tile"].sorties for r in (everything, air, attack)] == [3, 1, 2]
    assert [r.context["tile"].pilots for r in (everything, air, attack)] == [2, 1, 2]
    assert attack.context["tile"].kills_ground == 4  # not diluted by the air sortie
    assert all_time_attack.context["tile"].sorties == 2
    assert [r.context["role"] for r in (everything, air, attack)] == ["all", "air_superiority", "attack"]
    # loadouts: every role, or only the loadouts of the role
    assert {r.payload.payload_name for r in everything.context["loadouts"]} == {"Payload 1", "Payload 2"}
    assert {r.payload.payload_name for r in air.context["loadouts"]} == {"Payload 1"}
    assert {r.payload.payload_name for r in attack.context["loadouts"]} == {"Payload 2"}
    # the toggle links keep the tour, and the selector keeps the role
    body = attack.content.decode()
    assert (
        f"?tour={september.pk}&amp;role=air_superiority" in body
        or f"?role=air_superiority&amp;tour={september.pk}" in body
    )
    assert 'name="role" value="attack"' in body
    assert october.pk != september.pk


def test_detail_role_in_a_scope_nobody_flew_shows_zero_tiles(client: Client) -> None:
    _, october = history()

    response = client.get(f"{detail('MiG-15bis')}?tour={october.pk}&role=attack")

    assert response.status_code == 200
    assert (response.context["tile"].sorties, response.context["tile"].pilots) == (0, 0)


def test_unknown_role_means_all_roles(client: Client) -> None:
    history()

    response = client.get(f"{detail('MiG-15bis')}?tour=all&role=bogus")

    assert response.context["role"] == "all"
    assert response.context["tile"].sorties == 4


def test_matchups_follow_air_superiority_as_the_intercept_scope_and_ignore_attack(client: Client) -> None:
    history()
    url = f"{detail('MiG-15bis')}?tour=all"

    all_fights = client.get(url)
    air = client.get(url + "&role=air_superiority")
    attack = client.get(url + "&role=attack")

    assert all_fights.context["intercept"] is False
    assert air.context["intercept"] is True
    assert "Only kills where both pilots flew an air superiority sortie" in air.content.decode()
    assert "All kills and losses" not in air.content.decode()  # the intercept toggle is implied by the role
    assert attack.context["intercept"] is False
    assert attack.context["matchup_role_note"] is True
    assert "The role filter does not apply to matchups" in attack.content.decode()
    assert "The role filter does not apply to matchups" not in all_fights.content.decode()
    assert [m.enemy.log_name for m in attack.context["matchups"].rows] == [
        m.enemy.log_name for m in all_fights.context["matchups"].rows
    ]


@override_settings(IL2KS_LEADERBOARDS=SOME)
def test_top_pilot_tables_follow_the_role_and_hidden_players_stay_unnamed(client: Client) -> None:
    history()
    Player.objects.filter(account_uuid__endswith="000000000002").update(is_hidden=True)
    url = f"{detail('MiG-15bis')}?tour=all"

    everything = client.get(url).content.decode()
    air = client.get(url + "&role=air_superiority").content.decode()
    attack = client.get(url + "&role=attack").content.decode()

    assert "Top pilots by Elo" in everything
    assert "Top attack pilots" in everything
    assert "Top pilots by Elo" in air
    assert "Top attack pilots" not in air
    assert "Top attack pilots" in attack
    assert "Top pilots by Elo" not in attack
    assert "Player-1" in attack  # a visible attack pilot
    for body in (everything, air, attack):
        assert "Player-2" not in body  # hidden: counted in the totals, never named


def test_hidden_players_count_in_role_totals() -> None:
    september, _ = history()
    before = snapshot()
    Player.objects.filter(account_uuid__endswith="000000000002").update(is_hidden=True)

    rebuild_aggregates()

    assert snapshot() == before
    assert role_row("MiG-15bis", september, ATTACK).pilots == 2


def loadouts_of(client: Client, log_name: str) -> dict[str, Loadout]:
    with override_settings(IL2KS_LEADERBOARDS=SOME):
        response = client.get(f"{detail(log_name)}?tour=all")
    return {r.payload.payload_name: r for r in response.context["loadouts"]}


def test_loadout_measures_for_air_and_attack_loadouts(client: Client) -> None:
    history()

    air = loadouts_of(client, "F-86A-5")["Payload 1"]
    assert air.kills_per_sortie == pytest.approx(1 / 3)
    assert air.ground_hour is None
    assert air.elo is not None

    by_name = loadouts_of(client, "MiG-15bis")
    attack = by_name["Payload 2"]
    assert (attack.elo, attack.kills_per_sortie, attack.kd) == (None, None, None)
    # 4 tanks, 900 s on target: ground score per hour is the stored score over the time on target
    assert attack.ground_hour == pytest.approx(attack.payload.score_ground_attack * 3600 / 900)
    assert attack.payload.score_ground_attack > 0
    mig_air = by_name["Payload 1"]
    assert mig_air.kd == pytest.approx(mig_air.payload.kills_air_pvp / max(mig_air.payload.deaths, 1))


def test_loadout_rates_need_the_leaderboard_minimums(client: Client) -> None:
    history()
    strict = LeaderboardConfig(min_air_superiority_sorties=50, min_attack_sorties=50, min_time_on_target_minutes=999.0)

    with override_settings(IL2KS_LEADERBOARDS=strict):
        response = client.get(f"{detail('MiG-15bis')}?tour=all")
    for row in response.context["loadouts"]:
        assert (row.elo, row.kills_per_sortie, row.kd, row.ground_hour) == (None, None, None, None)
    body = response.content.decode()
    assert "Average Elo reflects the pilots' skill, not just the loadout's" in body or "pilots&#x27; skill" in body


def test_loadouts_sort_with_dashes_last_in_both_directions(client: Client) -> None:
    history()
    url = f"{detail('MiG-15bis')}?tour=all"

    def names(query: str) -> list[str]:
        with override_settings(IL2KS_LEADERBOARDS=SOME):
            response = client.get(url + query)
        return [r.payload.payload_name for r in response.context["loadouts"]]

    assert names("&lsort=ground_hour") == ["Payload 2", "Payload 1"]  # only the attack loadout has the measure
    assert names("&lsort=-ground_hour") == ["Payload 2", "Payload 1"]  # a dash is last whichever way
    assert names("&lsort=elo")[-1] == "Payload 2"
    assert names("&lsort=loadout") == ["Payload 1", "Payload 2"]
    assert names("&lsort=-loadout") == ["Payload 2", "Payload 1"]
    assert names("&lsort=password") == names("")  # whitelist: the default (sorties)


def test_loadout_sort_links_keep_tour_and_role(client: Client) -> None:
    september, _ = history()

    body = client.get(f"{detail('MiG-15bis')}?tour={september.pk}&role=attack").content.decode()

    assert "lsort=" in body
    link = next(line for line in body.splitlines() if "lsort=-sorties" in line or "lsort=sorties" in line)
    assert f"tour={september.pk}" in link
    assert "role=attack" in link


def test_list_follows_the_role_and_the_tour(client: Client) -> None:
    september, october = history()
    url = reverse("web:aircraft-list")

    def sorties(query: str) -> dict[str, int]:
        response = client.get(url + query)
        assert response.status_code == 200
        return {r.stats.aircraft.log_name: r.stats.sorties for r in response.context["rows"]}

    assert sorties(f"?tour={september.pk}") == {"MiG-15bis": 3, "F-86A-5": 2}
    assert sorties(f"?tour={september.pk}&role=attack") == {"MiG-15bis": 2}
    assert sorties(f"?tour={september.pk}&role=air_superiority") == {"MiG-15bis": 1, "F-86A-5": 2}
    assert sorties("?tour=all&role=attack") == {"MiG-15bis": 2}
    assert sorties("?tour=all&role=air_superiority") == {"MiG-15bis": 2, "F-86A-5": 3}
    assert sorties("?tour=all") == {"MiG-15bis": 4, "F-86A-5": 3}
    assert sorties(f"?tour={october.pk}&role=attack") == {}
    body = client.get(url + "?tour=all&role=attack").content.decode()
    assert "role=air_superiority" in body  # the toggle


def test_role_page_budgets(client: Client) -> None:
    september, _ = history()
    mig = detail("MiG-15bis")

    # tours + the stats row + the scoped row + hits + matchups + two top-pilot tables + loadouts + 2 context
    assert_simple_reads(client, f"{mig}?tour={september.pk}&role=attack", max_queries=10)
    assert_simple_reads(client, f"{mig}?tour=all&role=air_superiority", max_queries=10)
    assert_simple_reads(client, f"{mig}?tour=all&role=air_superiority&lsort=-elo", max_queries=10)
    assert_simple_reads(client, f"{mig}?tour=all", max_queries=9)
    assert_simple_reads(client, reverse("web:aircraft-list") + "?tour=all&role=attack", max_queries=5)
