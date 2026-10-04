"""Per-aircraft-type stats (FR-WEB-8): level-2 tables (incremental == rebuild) and the pages (TD-22, FR-ADM-3)."""

from datetime import timedelta

import pytest
from django.db import models
from django.test import Client, override_settings
from django.urls import reverse

from il2ks.config import LeaderboardConfig
from il2ks.core.replay.result import MissionResult
from il2ks.db.models import AircraftMatchup, AircraftPayload, AircraftStats, GameObject, Player, PlayerAircraft
from il2ks.ingest.aggregates import rebuild_aggregates
from tests.factories import STARTED_AT, kill, meta, mission, reindexed, save, sortie
from tests.simple_reads import assert_simple_reads

pytestmark = pytest.mark.django_db

AIR = "air_superiority"
MISSIONS = 5  # missions of the top-pilots seed
RATED = LeaderboardConfig(min_sorties=1, min_elo_games=1, min_attack_sorties=1, min_time_on_target_minutes=1.0)


def snapshot() -> dict[str, list[dict[str, object]]]:
    def rows(model: type[models.Model]) -> list[dict[str, object]]:
        found = list(model.objects.order_by("pk").values())
        for row in found:
            row.pop("id")
        return found

    return {"stats": rows(AircraftStats), "matchups": rows(AircraftMatchup), "payloads": rows(AircraftPayload)}


def first_mission() -> MissionResult:
    """MiG (REDFOR) players 1, 3 vs Sabre (BLUFOR) players 2, 4; player 5 flies an Il-10 attacker."""
    return mission(
        (
            sortie(0, 1, kills_air=2, kills_air_pvp=2, payload_id=1, combat_role=AIR),
            sortie(1, 2, aircraft_type="F-86A-5", coalition=2, is_death=True, is_plane_lost=True, payload_id=1),
            sortie(2, 3, is_death=True, is_plane_lost=True, payload_id=2),
            sortie(3, 4, aircraft_type="F-86A-5", coalition=2, kills_air=1, kills_air_pvp=1, payload_id=1),
            sortie(4, 5, aircraft_type="Il-10", kills_ground=2, combat_role="attack"),
            sortie(5, 6, aircraft_type="Turret_IL10", role="gunner"),
        ),
        (
            kill(100, 0, 1),
            kill(200, 0, 1, credit="assist"),  # an assist is not a kill
            kill(300, 3, 2, killer_type="F-86A-5", victim_type="MiG-15bis"),
            kill(400, 0, 3, is_friendly=True),  # friendly fire is not a matchup
            kill(500, 5, 1),  # a gunner's kill is not a pilot's
            kill(600, 0, None),  # AI victim: not a Kill row
        ),
    )


def test_totals_matchups_and_payloads_from_one_mission() -> None:
    save(first_mission())

    mig = AircraftStats.objects.get(aircraft__log_name="MiG-15bis")
    assert (mig.sorties, mig.pilots, mig.kills_air, mig.deaths, mig.planes_lost, mig.side) == (2, 2, 2, 1, 1, "redfor")
    assert (mig.kd, mig.kl, mig.survival) == (2.0, 2.0, 0.5)
    sabre = AircraftStats.objects.get(aircraft__log_name="F-86A-5")
    assert (sabre.sorties, sabre.side, sabre.survival) == (2, "blufor", 0.5)
    attacker = AircraftStats.objects.get(aircraft__log_name="Il-10")
    assert (attacker.attack_sorties, attacker.attack_share, attacker.kd) == (1, 1.0, 0.0)
    assert not AircraftStats.objects.filter(aircraft__log_name="Turret_IL10").exists()  # gunner sorties aren't counted

    pairs = {
        (m.killer_aircraft.log_name, m.victim_aircraft.log_name): m.kills
        for m in AircraftMatchup.objects.select_related("killer_aircraft", "victim_aircraft")
    }
    assert pairs == {("MiG-15bis", "F-86A-5"): 1, ("F-86A-5", "MiG-15bis"): 1}
    payloads = {
        (p.aircraft.log_name, p.payload_name): p.sorties for p in AircraftPayload.objects.select_related("aircraft")
    }
    assert payloads[("MiG-15bis", "Payload 1")] == 1
    assert payloads[("MiG-15bis", "Payload 2")] == 1


def test_incremental_equals_rebuild_with_a_reingest_that_drops_things() -> None:
    first = first_mission()
    save(first)
    save(
        mission(
            (
                sortie(0, 1, kills_air=1, kills_air_pvp=1, payload_id=3),
                sortie(1, 2, aircraft_type="F-51D", coalition=2, is_death=True, is_plane_lost=True),
            ),
            (kill(100, 0, 1, victim_type="F-51D"),),
        ),
        meta("2026-09-20_22-00-00", STARTED_AT + timedelta(days=1)),
    )
    # Re-ingest the first mission without the Sabre sorties: their types, kills and loadouts must go away.
    save(
        mission(
            reindexed((first.sorties[0], first.sorties[2], first.sorties[4])),
            (kill(100, 1, 0, killer_type="MiG-15bis", victim_type="MiG-15bis"),),
        )
    )
    incremental = snapshot()

    rebuild_aggregates()

    assert snapshot() == incremental
    assert not AircraftStats.objects.filter(aircraft__log_name="F-86A-5").exists()
    assert {(m.killer_aircraft.log_name, m.victim_aircraft.log_name) for m in AircraftMatchup.objects.all()} == {
        ("MiG-15bis", "MiG-15bis"),
        ("MiG-15bis", "F-51D"),
    }


def test_rebuild_repairs_drifted_rows() -> None:
    save(first_mission())
    good = snapshot()
    AircraftStats.objects.update(sorties=99, kd=9.0)
    AircraftMatchup.objects.all().delete()
    AircraftPayload.objects.update(sorties=7)

    rebuild_aggregates()

    assert snapshot() == good


def seed_with_hidden_top_pilot() -> None:
    """Player 1 (hidden) is the best MiG pilot, player 2 is second; player 3 loses once to the Sabre and player 9 in
    every later mission. Player 4 flies the Sabre and beats player 3 / 9."""
    for n in range(MISSIONS):
        save(
            mission(
                (
                    sortie(0, 1, kills_air=3, kills_air_pvp=3, combat_role=AIR),
                    sortie(1, 2, kills_air=1, kills_air_pvp=1, combat_role=AIR),
                    sortie(2, 3 if n == 0 else 9, is_death=True, is_plane_lost=True, combat_role=AIR),
                    sortie(3, 4, aircraft_type="F-86A-5", coalition=2, combat_role=AIR),
                ),
                (
                    kill(100, 0, 3, victim_type="F-86A-5"),
                    kill(200, 1, 3, victim_type="F-86A-5"),
                    kill(300, 3, 2, killer_type="F-86A-5", victim_type="MiG-15bis"),
                ),
            ),
            meta(f"2026-09-2{n}_10-00-00", STARTED_AT + timedelta(days=n)),
        )
    Player.objects.filter(account_uuid__endswith="000000000001").update(is_hidden=True)
    rebuild_aggregates()


def detail_url(log_name: str) -> str:
    return reverse("web:aircraft-detail", args=[GameObject.objects.get(log_name=log_name).pk])


def test_list_page_sorts_links_and_stays_within_budget(client: Client) -> None:
    save(first_mission())

    assert_simple_reads(client, reverse("web:aircraft-list"), max_queries=4)

    body = client.get(reverse("web:aircraft-list") + "?sort=-kills_air").content.decode()
    assert detail_url("MiG-15bis") in body
    assert "stretched-link" in body
    assert body.index("MiG-15bis") < body.index("F-86A Sabre")  # most air kills first
    ascending = client.get(reverse("web:aircraft-list") + "?sort=kills_air").content.decode()
    assert ascending.index("F-86A Sabre") < ascending.index("MiG-15bis")
    assert client.get(reverse("web:aircraft-list") + "?sort=password").status_code == 200  # whitelist: falls back


def test_detail_page_matchups_loadouts_and_budget(client: Client) -> None:
    save(first_mission())

    assert_simple_reads(client, detail_url("MiG-15bis"), max_queries=9)

    body = client.get(detail_url("MiG-15bis")).content.decode()
    assert "Matchups" in body
    assert detail_url("F-86A-5") in body  # the enemy type links to its own page
    assert "Payload 1" in body


@override_settings(IL2KS_LEADERBOARDS=RATED)
def test_top_pilots_are_ranked_by_type_elo_and_leave_out_hidden_players(client: Client) -> None:
    """OQ-49: skill, not volume. Hidden players are rated and counted in the totals but never named."""
    seed_with_hidden_top_pilot()

    body = client.get(detail_url("MiG-15bis")).content.decode()

    assert "Top pilots by Elo" in body
    assert "Player-1" not in body  # hidden: not named, not linked
    assert body.index("Player-2") < body.index("Player-9")  # the winner above the pilot who lost
    mig = AircraftStats.objects.get(aircraft__log_name="MiG-15bis")
    assert mig.kills_air == MISSIONS * (3 + 1)  # the hidden player's kills are in the total
    assert mig.sorties == MISSIONS * 3
    assert mig.pilots == 4  # players 1, 2, 3 and 9
    hidden = PlayerAircraft.objects.get(player__account_uuid__endswith="000000000001", aircraft=mig.aircraft)
    best_visible = PlayerAircraft.objects.get(player__account_uuid__endswith="000000000002", aircraft=mig.aircraft)
    assert hidden.elo > best_visible.elo > 1500.0  # the hidden player really is the best: ranked, not shown


def test_top_pilots_need_enough_rated_games(client: Client) -> None:
    seed_with_hidden_top_pilot()

    with override_settings(IL2KS_LEADERBOARDS=LeaderboardConfig(min_elo_games=3)):
        body = client.get(detail_url("MiG-15bis")).content.decode()
    assert "Player-2" in body
    assert "Player-3" not in body  # one rated game, under the minimum
    with override_settings(IL2KS_LEADERBOARDS=LeaderboardConfig(min_elo_games=99)):
        body = client.get(detail_url("MiG-15bis")).content.decode()
    assert "No pilot has enough rated games in this aircraft yet." in body


@override_settings(IL2KS_LEADERBOARDS=RATED)
def test_an_attack_type_ranks_its_pilots_by_ground_score_per_hour(client: Client) -> None:
    """FR-WEB-20 / OQ-49: Pounder's 3 tanks in 10 minutes beat Fencer's 10 fences in 2 hours; the attack type lists the
    ground ranking first."""
    save(
        mission(
            (
                sortie(
                    0,
                    1,
                    name="Pounder",
                    aircraft_type="Il-10",
                    combat_role="attack",
                    ground_by_category={"tank": 3},
                    time_on_target_s=600.0,
                ),
                sortie(
                    1,
                    2,
                    name="Fencer",
                    aircraft_type="Il-10",
                    combat_role="attack",
                    ground_by_category={"other": 10},
                    time_on_target_s=7200.0,
                ),
            ),
            (),
        )
    )

    body = client.get(detail_url("Il-10")).content.decode()

    assert "Top attack pilots" in body
    assert body.index("Pounder") < body.index("Fencer")
    assert "Top pilots by Elo" not in body  # nobody has rated games in it: the secondary ranking is left out


def test_a_fighter_type_without_attack_work_hides_the_ground_ranking(client: Client) -> None:
    save(first_mission())

    body = client.get(detail_url("F-86A-5")).content.decode()

    assert "Top pilots by Elo" in body
    assert "Top attack pilots" not in body


def test_unknown_or_unflown_aircraft_is_a_404(client: Client) -> None:
    save(first_mission())
    turret = GameObject.objects.get(log_name="Turret_IL10")  # registered, but only gunner sorties
    assert client.get(reverse("web:aircraft-detail", args=[turret.pk])).status_code == 404
    assert client.get(reverse("web:aircraft-detail", args=[999_999])).status_code == 404
