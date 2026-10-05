"""All-time tiers of the cumulative medals (x5 the per-tour thresholds) and the tours-in-a-row medal (FR-WEB-26, doc 17,
OQ-128, maintainer 2026-10-05): the tour keeps today's tiers, the all-time view of a cumulative medal counts the summed
tours against five times the (possibly admin-changed) tiers; "tours in a row" is all time only and reads the
`PlayerTour` rows."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from django.test import Client

from il2ks.db.models import PlayerAchievement, PlayerSortie, Tour
from il2ks.ingest.achievements import recompute_with_wanted_rules
from il2ks.ingest.aggregates import rebuild_aggregates
from tests.factories import STARTED_AT, meta, mission, save, sortie
from tests.integration.test_achievement_config import configure
from tests.integration.test_achievements import held, pk, snapshot
from tests.ops_helpers import make_instance

pytestmark = pytest.mark.django_db


def month(n: int) -> datetime:
    """A moment in tour `n` (tours are calendar months; 30 days from the 19th of September: a new month every time)."""
    return STARTED_AT + timedelta(days=30 * n)


def fly(n: int, player: int = 1, kills_air: int = 0) -> None:
    """One sortie of `player` in tour `n`."""
    save(mission((sortie(0, player, kills_air=kills_air),)), meta(f"t{n}p{player}", month(n)))


def tours() -> list[Tour]:
    return list(Tour.objects.order_by("started_at"))


def all_time_row(n: int, key: str, tier: int) -> PlayerAchievement:
    return PlayerAchievement.objects.get(player_id=pk(n), tour=None, key=key, tier=tier)


# --- the x5 tiers ---------------------------------------------------------------------------------------------------
def test_a_cumulative_medal_counts_five_times_the_thresholds_all_time_and_the_base_ones_per_tour() -> None:
    fly(0, kills_air=12)  # 12 kills in the tour: 10 reached; all time that is 5 reached, 50 not
    assert held(1, tours()[0])["career_kills"] == 2
    assert held(1)["career_kills"] == 1

    save(mission((sortie(0, 1, kills_air=20), sortie(1, 1, kills_air=10))), meta("t1b", month(1)))
    # 12 + 30 = 42 kills: still tier 1 (the 50 are not there)
    assert held(1)["career_kills"] == 1
    fly(2, kills_air=10)  # 52: the 50 of tier 2

    assert held(1)["career_kills"] == 2
    assert all_time_row(1, "career_kills", 2).mission.mission_uid == "t2p1"
    assert [held(1, t)["career_kills"] for t in tours()] == [2, 2, 2]  # 12, 30, 10 kills: 10 reached in each


def test_the_crossing_sortie_comes_from_the_replay_with_the_earlier_tours_carried_in() -> None:
    fly(0, kills_air=30)
    save(mission((sortie(0, 1, kills_air=20), sortie(1, 1, kills_air=10))), meta("t1b", month(1)))

    row = all_time_row(1, "career_kills", 2)  # 30 + 20 = 50 at the first sortie of the second tour
    assert row.mission.mission_uid == "t1b"
    first = PlayerSortie.objects.filter(mission_id=row.mission_id, player_id=pk(1)).order_by("spawned_at", "pk").first()
    assert first is not None
    assert row.sortie_id == first.pk


def test_admin_changed_per_tour_thresholds_scale_into_the_all_time_ones(tmp_path: Path) -> None:
    fly(0, kills_air=30)
    fly(1, kills_air=30)
    cfg = make_instance(tmp_path, with_db=False)
    assert held(1)["career_kills"] == 2  # 60 kills against 5, 50, 250, 1250

    configure(thresholds={"career_kills": [2, 20, 100, 500]})  # all time: 10, 100, 500, 2500
    assert recompute_with_wanted_rules(cfg)

    assert held(1)["career_kills"] == 1  # 60 kills: 10 reached, 100 not
    assert held(1, tours()[0])["career_kills"] == 2  # 30 kills in the tour: 20 reached
    incremental = snapshot()
    rebuild_aggregates()
    assert snapshot() == incremental


def test_the_all_time_view_shows_the_scaled_numbers_and_a_tour_the_base_ones(client: Client) -> None:
    fly(0, kills_air=3)
    first = tours()[0]

    in_tour = client.get(f"/achievements/?tour={first.pk}").content.decode()
    all_time = client.get("/achievements/?tour=all").content.decode()

    assert "<span>250</span>" in in_tour
    assert "<span>1250</span>" not in in_tour
    assert "<span>1250</span>" in all_time
    assert "<span>250</span>" in all_time  # the third tier (50 x 5)
    flight_hours_in_tour = in_tour.split("Hours Aloft")[1]
    assert "<span>200 h</span>" in flight_hours_in_tour
    assert "<span>1000 h</span>" in all_time.split("Hours Aloft")[1]


def test_the_profile_and_the_holders_page_show_the_all_time_numbers(client: Client) -> None:
    fly(0, kills_air=12)
    first = tours()[0]

    assert "Bronze: 5." in client.get(f"/players/{pk(1)}/?tour=all").content.decode()
    assert "Silver: 10." in client.get(f"/players/{pk(1)}/?tour={first.pk}").content.decode()
    assert "Silver · 50" in client.get("/achievements/career_kills/?tour=all&tier=2").content.decode()
    assert "Silver · 10" in client.get(f"/achievements/career_kills/?tour={first.pk}&tier=2").content.decode()


# --- tours in a row ---------------------------------------------------------------------------------------------------
def test_tours_in_a_row_needs_flying_in_consecutive_tours_and_a_gap_resets() -> None:
    fly(0)
    assert "tours_in_a_row" not in held(1)
    fly(1)
    assert held(1)["tours_in_a_row"] == 1  # two tours in a row
    fly(2, player=2)  # tour 2 exists (somebody flew in it) but player 1 skipped it: a gap
    fly(3)
    assert held(1)["tours_in_a_row"] == 1
    fly(4)  # 3, 4: only two in a row, the earlier run of two did not carry over
    assert held(1)["tours_in_a_row"] == 1
    fly(5)  # 3, 4, 5
    assert held(1)["tours_in_a_row"] == 2


def test_tours_in_a_row_is_earned_at_the_first_sortie_of_the_completing_tour() -> None:
    fly(0)
    save(mission((sortie(0, 1), sortie(1, 1))), meta("t1b", month(1)))

    row = all_time_row(1, "tours_in_a_row", 1)
    assert row.mission.mission_uid == "t1b"
    first = PlayerSortie.objects.filter(mission_id=row.mission_id, player_id=pk(1)).order_by("spawned_at", "pk").first()
    assert first is not None
    assert row.sortie_id == first.pk
    assert row.earned_at == first.ended_at


def test_the_running_tour_counts_once_the_pilot_flies_in_it() -> None:
    fly(0)
    fly(1)
    fly(2)  # three tours in a row (tier 2); this is the newest tour
    assert held(1)["tours_in_a_row"] == 2
    fly(3, player=2)  # a newer tour starts; player 1 has not flown in it yet: the medal stays, nothing is lost
    assert held(1)["tours_in_a_row"] == 2
    assert held(2) == {}  # player 2 flew in one tour only


def test_a_tour_never_has_a_tours_in_a_row_row() -> None:
    fly(0)
    fly(1)

    assert PlayerAchievement.objects.filter(key="tours_in_a_row", tour__isnull=False).count() == 0
    assert PlayerAchievement.objects.filter(key="tours_in_a_row", tour=None).count() == 1


def test_a_late_import_into_an_old_tour_that_fills_a_gap_extends_the_run() -> None:
    fly(0)
    fly(1)
    fly(2, player=2)  # tour 2 exists (somebody flew in it), player 1 has not been imported yet
    fly(3)
    fly(4)  # runs of 2 and 2, a gap at tour 2
    assert held(1)["tours_in_a_row"] == 1

    fly(2)  # imported late: tours 0 to 4 are one run of 5

    assert held(1)["tours_in_a_row"] == 2
    row = all_time_row(1, "tours_in_a_row", 2)
    assert row.mission.mission_uid == "t2p1"  # three in a row reached at tour 2, the first run that has three
    incremental = snapshot()
    rebuild_aggregates()
    assert snapshot() == incremental


def test_tours_in_a_row_incremental_equals_rebuild() -> None:
    for n in range(10):
        fly(n, player=2 if n == 3 else 1)  # player 1 skips tour 3 (only player 2 flew in it)
        if n % 2 == 0:
            fly(n, player=2)
    incremental = snapshot()
    assert held(1)["tours_in_a_row"] == 3  # tours 4 to 9: six in a row
    assert {r.tier for r in PlayerAchievement.objects.filter(player_id=pk(1), key="tours_in_a_row")} == {1, 2, 3}

    rebuild_aggregates()

    assert snapshot() == incremental


def test_tours_in_a_row_is_all_time_only_on_the_pages(client: Client) -> None:
    fly(0)
    fly(1)
    first = tours()[0]

    assert "Old Hand" in client.get("/achievements/?tour=all").content.decode()
    assert "Old Hand" not in client.get(f"/achievements/?tour={first.pk}").content.decode()
    assert client.get(f"/achievements/tours_in_a_row/?tour={first.pk}").status_code == 404
    assert "Old Hand" in client.get(f"/players/{pk(1)}/?tour=all").content.decode()
    assert "Old Hand" in client.get(f"/players/{pk(1)}/achievements/?tour=all").content.decode()
    assert "Old Hand" not in client.get(f"/players/{pk(1)}/achievements/?tour={first.pk}").content.decode()
    holders = client.get("/achievements/tours_in_a_row/?tour=all").content.decode()
    assert "2 tours" in holders


def test_tours_in_a_row_can_be_switched_off_and_its_thresholds_changed(tmp_path: Path) -> None:
    for n in range(4):
        fly(n)
    cfg = make_instance(tmp_path, with_db=False)
    assert held(1)["tours_in_a_row"] == 2  # 2 and 3 reached

    configure(thresholds={"tours_in_a_row": [3, 4, 5, 6]})
    assert recompute_with_wanted_rules(cfg)
    assert held(1)["tours_in_a_row"] == 2  # 3 and 4 reached

    configure(off=["tours_in_a_row"])
    assert recompute_with_wanted_rules(cfg)
    assert "tours_in_a_row" not in held(1)
