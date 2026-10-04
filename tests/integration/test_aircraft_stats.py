"""Per-aircraft-type stats (FR-WEB-8): level-2 tables (incremental == rebuild) and the pages (TD-22, FR-ADM-3)."""

from datetime import timedelta
from pathlib import Path

import pytest
from django.db import models
from django.test import Client, override_settings
from django.urls import reverse

from il2ks.config import LeaderboardConfig
from il2ks.core.replay.result import MissionResult
from il2ks.db.models import (
    AircraftMatchup,
    AircraftPayload,
    AircraftStats,
    GameObject,
    Player,
    PlayerAircraft,
    SiteSettings,
    Tour,
    TourAircraftStats,
)
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.web import object_names
from tests.factories import STARTED_AT, kill, meta, mission, reindexed, save, sortie
from tests.ops_helpers import make_instance
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

    return {
        "stats": rows(AircraftStats),
        "tour_stats": rows(TourAircraftStats),
        "matchups": rows(AircraftMatchup),
        "payloads": rows(AircraftPayload),
    }


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
    sabre = AircraftStats.objects.get(aircraft__log_name="F-86A-5")
    assert (sabre.sorties, sabre.side) == (2, "blufor")
    attacker = AircraftStats.objects.get(aircraft__log_name="Il-10")
    assert attacker.attack_sorties == 1
    assert not {"kd", "kl", "survival", "attack_share"} & {f.name for f in AircraftStats._meta.get_fields()}  # OQ-98
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
    AircraftStats.objects.update(sorties=99, kills_air=9)
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

    assert_simple_reads(client, reverse("web:aircraft-list"), max_queries=5)  # 4 + the tours of the selector

    body = client.get(reverse("web:aircraft-list") + "?tour=all&sort=-kills_air").content.decode()
    assert detail_url("MiG-15bis") in body
    assert "stretched-link" in body
    assert body.index("MiG-15bis") < body.index("F-86A Sabre")  # most air kills first
    ascending = client.get(reverse("web:aircraft-list") + "?tour=all&sort=kills_air").content.decode()
    assert ascending.index("F-86A Sabre") < ascending.index("MiG-15bis")
    assert client.get(reverse("web:aircraft-list") + "?sort=password").status_code == 200  # whitelist: falls back


def test_list_sorted_by_aircraft_follows_the_localized_name(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """`?sort=aircraft` orders by the name the page shows (viewer's language), not the English `display_name`."""
    save(first_mission())
    names = {"MiG-15bis": "Alpha-localized", "F-86A-5": "Zulu-localized"}

    def fake_name_of(obj: object, language: str, catalog: object = None) -> str:
        return names.get(str(getattr(obj, "log_name", "")), "Other")

    monkeypatch.setattr(object_names, "name_of", fake_name_of)
    url = reverse("web:aircraft-list")

    ascending = client.get(url + "?sort=aircraft").content.decode()
    descending = client.get(url + "?sort=-aircraft").content.decode()

    assert ascending.index("Alpha-localized") < ascending.index("Zulu-localized")
    assert descending.index("Zulu-localized") < descending.index("Alpha-localized")


def test_detail_page_matchups_loadouts_and_budget(client: Client) -> None:
    save(first_mission())

    assert_simple_reads(client, detail_url("MiG-15bis"), max_queries=10)  # 9 + the current tour's counters

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
    assert "No pilot has enough encounters in this aircraft yet." in body


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


# --- per tour (FR-WEB-8, TD-26): `?tour=` as on the leaderboards; absent = the current tour, `all` = all time ---------
OCTOBER = STARTED_AT + timedelta(days=40)  # September 2026 holds the first mission, October the second


def two_tours() -> tuple[Tour, Tour]:
    """September: two MiG sorties (players 1, 3) and a Sabre (2). October: one MiG (player 1) and a Mustang (2)."""
    save(
        mission(
            (
                sortie(0, 1, kills_air=2, kills_air_pvp=2, payload_id=1, combat_role=AIR),
                sortie(1, 2, aircraft_type="F-86A-5", coalition=2, is_death=True, is_plane_lost=True),
                sortie(2, 3, is_death=True, is_plane_lost=True, payload_id=2),
            ),
            (kill(100, 0, 1, victim_type="F-86A-5"), kill(200, 1, 2, killer_type="F-86A-5", victim_type="MiG-15bis")),
        )
    )
    save(
        mission(
            (sortie(0, 1, kills_air=1, kills_air_pvp=1), sortie(1, 2, aircraft_type="F-51D", coalition=2)),
            (kill(100, 0, 1, victim_type="F-51D"),),
        ),
        meta("2026-10-29_22-00-00", OCTOBER),
    )
    september, october = Tour.objects.order_by("started_at")
    return september, october


def listed(client: Client, query: str = "") -> dict[str, int]:
    response = client.get(reverse("web:aircraft-list") + query)
    assert response.status_code == 200
    return {row.stats.aircraft.log_name: row.stats.sorties for row in response.context["rows"]}


def test_list_follows_the_tour_selector_like_the_other_pages(client: Client) -> None:
    september, october = two_tours()

    assert listed(client) == {"MiG-15bis": 1, "F-51D": 1}  # no ?tour=: the current (newest) tour
    assert listed(client, f"?tour={october.pk}") == {"MiG-15bis": 1, "F-51D": 1}
    assert listed(client, f"?tour={september.pk}") == {"MiG-15bis": 2, "F-86A-5": 1}
    assert listed(client, "?tour=all") == {"MiG-15bis": 3, "F-86A-5": 1, "F-51D": 1}
    assert listed(client, "?tour=999999") == {"MiG-15bis": 1, "F-51D": 1}  # unknown id: the current tour, no error
    pilots = {
        row.stats.aircraft.log_name: row.stats.pilots
        for row in client.get(reverse("web:aircraft-list") + f"?tour={september.pk}").context["rows"]
    }
    assert pilots == {"MiG-15bis": 2, "F-86A-5": 1}  # distinct players of the tour


def test_list_per_tour_sorts_and_keeps_all_time_hits_to_destroy(client: Client) -> None:
    september, _ = two_tours()
    url = reverse("web:aircraft-list") + f"?tour={september.pk}"

    ascending = client.get(url + "&sort=kills_air").content.decode()
    descending = client.get(url + "&sort=-kills_air&cols=kills_air_pvp").content.decode()

    assert ascending.index("F-86A Sabre") < ascending.index("MiG-15bis")
    assert descending.index("MiG-15bis") < descending.index("F-86A Sabre")
    assert "F-51D" not in descending  # not flown in September
    assert 'name="tour"' in descending  # the selector
    assert_simple_reads(client, url, max_queries=5)
    assert_simple_reads(client, reverse("web:aircraft-list") + "?tour=all", max_queries=5)


def test_list_of_a_tour_nobody_flew_in_is_empty(client: Client) -> None:
    two_tours()
    empty = Tour.objects.create(title="December 2026", started_at=OCTOBER + timedelta(days=60), ended_at=None, mode="x")

    response = client.get(reverse("web:aircraft-list") + f"?tour={empty.pk}")

    assert response.status_code == 200
    assert "No aircraft has flown in this tour yet." in response.content.decode()


def test_detail_tiles_follow_the_tour_but_the_rest_stays_all_time(client: Client) -> None:
    september, october = two_tours()
    mig = GameObject.objects.get(log_name="MiG-15bis")
    url = reverse("web:aircraft-detail", args=[mig.pk])

    in_september = client.get(f"{url}?tour={september.pk}")
    in_october = client.get(url)  # the current tour
    all_time = client.get(f"{url}?tour=all")

    assert [r.context["tile"].sorties for r in (in_september, in_october, all_time)] == [2, 1, 3]
    assert [r.context["tile"].pilots for r in (in_september, in_october, all_time)] == [2, 1, 2]
    assert [r.context["tile"].kills_air for r in (in_september, in_october, all_time)] == [2, 1, 3]
    assert [in_september.context["survived"], in_october.context["survived"]] == [1, 1]  # 2 sorties 1 death; 1 and 0
    assert all_time.context["stats"].sorties == 3  # the all-time row is always loaded (side badge)
    for response in (in_september, in_october, all_time):  # loadouts stay all time
        assert {p.payload_name: p.sorties for p in response.context["payloads"]} == {
            "Payload 1": 2,
            "Payload 2": 1,
        }
    # the matchups follow the same tour
    assert [m.enemy.log_name for m in in_september.context["matchups"].rows] == ["F-86A-5"]
    assert [m.enemy.log_name for m in in_october.context["matchups"].rows] == ["F-51D"]
    assert 'name="tour"' in in_october.content.decode()
    assert october.pk != september.pk


def test_detail_in_a_tour_without_the_type_shows_zero_tiles(client: Client) -> None:
    two_tours()
    sabre = GameObject.objects.get(log_name="F-86A-5")

    response = client.get(reverse("web:aircraft-detail", args=[sabre.pk]))  # October: only a Mustang flew

    assert response.status_code == 200
    tile = response.context["tile"]
    assert (tile.sorties, tile.pilots, tile.kills_air) == (0, 0, 0)
    assert response.context["stats"].sorties == 1  # flown all-time, so no 404


def test_detail_budget_with_a_tour(client: Client) -> None:
    september, _ = two_tours()
    mig = GameObject.objects.get(log_name="MiG-15bis")

    assert_simple_reads(client, reverse("web:aircraft-detail", args=[mig.pk]) + f"?tour={september.pk}", max_queries=10)
    assert_simple_reads(client, reverse("web:aircraft-detail", args=[mig.pk]) + "?tour=all", max_queries=9)


def test_reingest_into_another_tour_moves_the_per_tour_rows() -> None:
    """A re-ingest whose start time moves the mission to another tour recomputes the old and the new tour."""
    save(mission((sortie(0, 1, kills_air=1),)), meta("m1", STARTED_AT))
    assert list(TourAircraftStats.objects.values_list("tour__title", "sorties")) == [("September 2026", 1)]

    save(mission((sortie(0, 1, kills_air=1),)), meta("m1", OCTOBER))

    assert list(TourAircraftStats.objects.values_list("tour__title", "sorties")) == [("October 2026", 1)]


def test_the_migration_backfill_builds_the_tour_rows_of_an_old_database(tmp_path: Path) -> None:
    from il2ks.ops import migrate

    two_tours()
    good = snapshot()
    TourAircraftStats.objects.all().delete()
    SiteSettings.objects.filter(pk=1).update(backfills_done=[])

    migrate._run_backfills(make_instance(tmp_path), [migrate.BACKFILL_TOUR_AIRCRAFT])  # pyright: ignore[reportPrivateUsage]

    assert snapshot() == good
    assert migrate._already_done(migrate.BACKFILL_TOUR_AIRCRAFT)  # pyright: ignore[reportPrivateUsage]
