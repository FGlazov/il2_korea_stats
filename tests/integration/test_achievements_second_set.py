"""The second set of achievements (doc 17, OQ-105) at ingest: the Elo peak per sortie, the stored facts (rams, first
blood, multi-kills), incremental == rebuild, gunners and the upgrade backfill."""

from datetime import timedelta
from pathlib import Path

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from il2ks.config import Config
from il2ks.core.replay.result import CombatRole, KillResult, MissionResult
from il2ks.db.models import AchievementHolders, Player, PlayerAchievement, PlayerSortie, SiteSettings, Tour
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.persist import MissionMeta
from il2ks.ingest.ratings import recompute_ratings
from il2ks.ops import migrate
from tests.factories import SERVER_UID, STARTED_AT, account, kill, meta, mission, save, sortie

pytestmark = pytest.mark.django_db

DAY2 = STARTED_AT + timedelta(days=1)


def pk(n: int) -> int:
    return Player.objects.get(account_uuid=account(n)).pk


def held(n: int) -> dict[str, int]:
    best: dict[str, int] = {}
    for row in PlayerAchievement.objects.filter(player_id=pk(n)):
        best[row.key] = max(best.get(row.key, 0), row.tier)
    return best


def snapshot() -> list[tuple[object, ...]]:
    rows = PlayerAchievement.objects.order_by("player_id", "key", "tier").values_list(
        "player_id", "key", "tier", "earned_at", "sortie_id", "mission_id"
    )
    counts = AchievementHolders.objects.order_by("key", "tier").values_list("key", "tier", "holders")
    peaks = PlayerSortie.objects.order_by("pk").values_list("pk", "elo_peak")
    return [*rows, *counts, *peaks]


def duel(n_wins: int, *, first: int = 1, start: int = 1) -> tuple[MissionResult, MissionMeta]:
    """Pilot `first` (MiG-15bis, coalition 1) shoots down `n_wins` different air-superiority pilots (F-86A-5)."""
    air: CombatRole = "air_superiority"
    sorties = [sortie(0, first, combat_role=air, kills_air=n_wins, kills_air_pvp=n_wins)]
    kills: list[KillResult] = []
    for i in range(n_wins):
        sorties.append(
            sortie(i + 1, start + i + 1, aircraft_type="F-86A-5", coalition=2, combat_role=air, is_death=True)
        )
        kills.append(kill(100 + i, 0, i + 1))
    return mission(tuple(sorties), tuple(kills)), meta("duel", STARTED_AT)


def test_the_elo_peak_is_stored_on_the_winning_sortie_and_earns_the_medal() -> None:
    save(*duel(8))

    winner = PlayerSortie.objects.get(player_id=pk(1))
    losers = PlayerSortie.objects.filter(is_death=True)
    assert winner.elo_peak == pytest.approx(Player.objects.get(pk=pk(1)).elo_jet, abs=1e-9)
    assert winner.elo_peak > 1550
    assert all(s.elo_peak == 0 for s in losers)  # a loser's rating only falls: no peak
    assert held(1)["elo_peak"] >= 1
    assert "elo_peak" not in held(2)


def test_a_jet_beating_a_prop_changes_no_rating_and_earns_no_peak() -> None:
    air: CombatRole = "air_superiority"
    save(
        mission(
            (
                sortie(0, 1, combat_role=air, kills_air=1, kills_air_pvp=1),
                sortie(1, 2, aircraft_type="P-51D-15", coalition=2, combat_role=air, is_death=True),
            ),
            (kill(100, 0, 1),),
        )
    )

    assert not PlayerSortie.objects.filter(elo_peak__gt=0).exists()


def test_elo_peaks_follow_the_global_replay_order_incremental_equals_rebuild() -> None:
    save(*duel(8, first=1))
    save(
        mission(
            (
                sortie(0, 9, combat_role="air_superiority", kills_air=3, kills_air_pvp=3),
                *(
                    sortie(
                        i, 20 + i, aircraft_type="F-86A-5", coalition=2, combat_role="air_superiority", is_death=True
                    )
                    for i in range(1, 4)
                ),
            ),
            tuple(kill(100 + i, 0, i + 1) for i in range(3)),
        ),
        meta("earlier", STARTED_AT - timedelta(days=1)),  # saved last, but played first: it moves every later rating
    )
    incremental = snapshot()

    rebuild_aggregates()

    assert snapshot() == incremental
    assert PlayerSortie.objects.filter(elo_peak__gt=0).count() == 2


def test_stored_facts_feed_the_medals() -> None:
    save(
        mission(
            (
                sortie(0, 1, kills_air=3, kills_air_pvp=1, rams=1, first_blood=True, multi_kill=3),
                sortie(1, 2, kills_air=1, multi_kill=1),
            )
        ),
        meta("m1", STARTED_AT),
    )

    assert held(1)["ram"] == 1
    assert held(1)["first_blood"] == 1
    assert held(1)["multi_kill"] == 2  # a triple: bronze (double) and silver
    assert "multi_kill" not in held(2)
    assert "ram" not in held(2)


def test_gunner_sorties_earn_nothing_from_the_new_facts() -> None:
    save(
        mission(
            (
                sortie(0, 1),
                sortie(1, 4, role="gunner", aircraft_type="Turret_IL10", rams=1, first_blood=True, multi_kill=4),
            )
        )
    )

    assert not PlayerAchievement.objects.filter(player_id=pk(4)).exists()


def test_shame_medals_and_hidden_missions() -> None:
    save(
        mission(
            (
                sortie(0, 1, outcome="not_taken_off", flight_time_s=0.0, taxi_accident=True, is_plane_lost=True),
                sortie(1, 2, outcome="crashed", is_death=True, friendly_kills=1),
                sortie(2, 2, outcome="crashed", is_death=True),
                sortie(3, 2, outcome="crashed", is_death=True),
            )
        ),
        meta("m1", STARTED_AT),
    )

    assert held(1) == {"shame_taxi": 1}  # a taxi accident is not a "crash after take-off"
    assert held(2)["shame_friendly"] == 1
    assert held(2)["shame_crashed"] == 1  # three crashes after take-off
    assert AchievementHolders.objects.get(key="shame_taxi", tier=1, tour=None).holders == 1


def test_landing_streak_and_types_are_stored_nothing_extra() -> None:
    for day in range(6):
        save(
            mission((sortie(0, 1, aircraft_type="MiG-15bis" if day % 2 else "F-86A-5"),)),
            meta(f"m{day}", STARTED_AT + timedelta(hours=day)),
        )

    assert held(1)["landing_streak"] == 2  # six landings in a row
    assert "types_flown" not in held(1)  # two types are not enough for the ribbon


def test_the_upgrade_backfill_derives_the_facts_from_stored_data() -> None:
    save(
        mission(
            (
                sortie(0, 1, kills_air=3),
                sortie(1, 2, coalition=2, kills_air=1),
                sortie(2, 3, coalition=2, is_death=True),
            ),
            # p1 draws first blood on p3; p1 and p2 then kill each other at once: a ram
            (kill(50, 0, 2), kill(100, 0, 1), kill(101, 1, 0)),
        ),
        meta("m1", STARTED_AT),
    )
    first = PlayerSortie.objects.get(player_id=pk(1))
    first.timeline = [
        {"kind": "kill", "tick": t, "counterpart": {"object_type": "F-86A-5", "sortie_id": None, "coalition": 2}}
        for t in (100, 2000, 2500, 9000)  # a burst of three within a minute (50 ticks per second), then a lone kill
    ]
    first.save(update_fields=["timeline"])
    PlayerSortie.objects.update(rams=0, first_blood=False, multi_kill=0)
    SiteSettings.objects.filter(pk=1).update(backfills_done=[])
    PlayerAchievement.objects.all().delete()
    config = Config(data_dir=Path("."), server_uid=SERVER_UID, timezone_name="UTC")

    migrate._run_backfills(config, [migrate.BACKFILL_ACHIEVEMENT_FACTS])  # pyright: ignore[reportPrivateUsage]

    rows = {s.player_id: s for s in PlayerSortie.objects.all()}
    assert rows[pk(1)].first_blood
    assert not rows[pk(2)].first_blood
    assert (rows[pk(1)].rams, rows[pk(2)].rams, rows[pk(3)].rams) == (1, 1, 0)
    assert (rows[pk(1)].multi_kill, rows[pk(2)].multi_kill) == (3, 0)
    assert held(1)["multi_kill"] == 2  # the rebuild computed the medals from the derived facts
    assert held(1)["ram"] == 1


def test_the_backfill_first_blood_skips_gunner_kills() -> None:
    """The replay's first blood ignores gunners (`first_blood_sortie`); the backfill must agree (doc 17)."""
    save(
        mission(
            (
                sortie(0, 1, kills_air=1, first_blood=True),
                sortie(1, 4, role="gunner", aircraft_type="Turret_IL10"),
                sortie(2, 3, coalition=2, is_death=True),
                sortie(3, 5, coalition=2, is_death=True),
            ),
            (kill(50, 1, 2), kill(100, 0, 3)),  # the gunner kills first, then the pilot
        ),
        meta("m1", STARTED_AT),
    )
    ingested = dict(PlayerSortie.objects.values_list("player_id", "first_blood"))
    assert ingested[pk(1)]
    assert not ingested[pk(4)]
    PlayerSortie.objects.update(rams=0, first_blood=False, multi_kill=0)
    SiteSettings.objects.filter(pk=1).update(backfills_done=[])
    config = Config(data_dir=Path("."), server_uid=SERVER_UID, timezone_name="UTC")

    migrate._run_backfills(config, [migrate.BACKFILL_ACHIEVEMENT_FACTS])  # pyright: ignore[reportPrivateUsage]

    rows = dict(PlayerSortie.objects.values_list("player_id", "first_blood"))
    assert rows[pk(1)]
    assert not rows[pk(4)]


def test_clearing_stored_elo_peaks_looks_up_their_owners_in_one_query() -> None:
    """The owners of cleared peaks are read together, not one query per peak (the number of reads does not grow)."""
    save(*duel(6))
    losers = list(PlayerSortie.objects.filter(is_death=True).order_by("pk").values_list("pk", flat=True))

    def clearing(count: int) -> int:
        PlayerSortie.objects.filter(pk__in=losers[:count]).update(elo_peak=1700.0)  # peaks no game explains
        with CaptureQueriesContext(connection) as queries:
            recompute_ratings()
        assert not PlayerSortie.objects.filter(pk__in=losers[:count], elo_peak__gt=0).exists()
        return sum(q["sql"].startswith("SELECT") for q in queries)  # the writes are one per row anyway

    assert clearing(5) == clearing(2)


def test_saving_a_mission_that_changes_elo_peaks_recounts_the_holders_once() -> None:
    with CaptureQueriesContext(connection) as queries:
        save(*duel(8))  # a new peak: the ratings step rewrites medals

    assert PlayerSortie.objects.filter(elo_peak__gt=0).exists()
    holder_reads = [q for q in queries if q["sql"].startswith("SELECT") and "il2ks_db_achievementholders" in q["sql"]]
    assert len(holder_reads) == 1


def test_top_rated_is_a_medal_of_every_tour_and_all_time_is_the_best_of_them(client: Client) -> None:
    """A new tour is a clean slate for achievements too (maintainer, 2026-10-05): `elo_peak` is no
    longer all-time only. Each tour's rows follow the sorties' stored Elo peaks of that tour (the rating starts over
    in every tour, so October needs two wins of its own to reach the first tier at 1530); all time = the max tier over
    the tours, each tier earned where the first tour earned it."""
    save(*duel(8))
    save(
        mission(
            (
                sortie(0, 1, combat_role="air_superiority", kills_air=2, kills_air_pvp=2),
                sortie(1, 40, aircraft_type="F-86A-5", coalition=2, combat_role="air_superiority", is_death=True),
                sortie(2, 41, aircraft_type="F-86A-5", coalition=2, combat_role="air_superiority", is_death=True),
            ),
            (kill(100, 0, 1), kill(110, 0, 2)),
        ),
        meta("october", STARTED_AT + timedelta(days=40)),
    )
    september, october = Tour.objects.order_by("started_at")

    assert held(1)["elo_peak"] >= 1  # all time
    assert PlayerAchievement.objects.filter(player_id=pk(1), tour=september, key="elo_peak").exists()
    assert PlayerAchievement.objects.filter(player_id=pk(1), tour=october, key="elo_peak").exists()
    first = PlayerAchievement.objects.get(player_id=pk(1), tour=september, key="elo_peak", tier=1)
    all_time = PlayerAchievement.objects.get(player_id=pk(1), tour=None, key="elo_peak", tier=1)
    assert (all_time.sortie_id, all_time.earned_at) == (first.sortie_id, first.earned_at)
    assert AchievementHolders.objects.filter(key="elo_peak", tour=october).exists()
    assert "Top Rated" in client.get(f"/players/{pk(1)}/achievements/?tour={october.pk}").content.decode()
    assert "Top Rated" in client.get(f"/achievements/?tour={october.pk}").content.decode()
