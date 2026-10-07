"""Side balance (OQ-134): mission averages, the per-player side counters and the underdog counter at every level."""

from datetime import timedelta

import pytest

from il2ks.db.models import Mission, Player, PlayerAircraft, PlayerSortie, PlayerTour, TourAircraftStats
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.counters import COUNTER_FIELDS
from tests.factories import STARTED_AT, account, meta, mission, save, sortie

pytestmark = pytest.mark.django_db

SIDE_FIELDS = ("sorties", "sorties_redfor", "sorties_blufor", "sorties_underdog")


def _first() -> None:
    save(
        mission(
            (
                sortie(0, 1, underdog=True),  # REDFOR
                sortie(1, 2, coalition=2, aircraft_type="F-86A-5"),  # BLUFOR
                sortie(2, 3, coalition=2, aircraft_type="F-86A-5", role="gunner", underdog=True),  # not counted
            ),
            redfor_players=1.0,
            blufor_players=1.5,
        )
    )


def test_mission_stores_the_time_weighted_averages() -> None:
    _first()
    m = Mission.objects.get()
    assert (m.redfor_players, m.blufor_players) == (1.0, 1.5)
    assert list(PlayerSortie.objects.order_by("spawn_tick").values_list("underdog", flat=True)) == [True, False, True]


def test_player_counters_count_sides_and_underdog_sorties() -> None:
    _first()
    save(
        mission((sortie(0, 1, coalition=2, aircraft_type="F-86A-5", underdog=True), sortie(1, 1, country=999))),
        meta("2026-09-20_22-00-00", STARTED_AT + timedelta(days=1)),
    )
    p1 = Player.objects.get(account_uuid=account(1))
    # 3 counted sorties: one REDFOR, one BLUFOR, one of a country that is neither; two of them underdog
    assert (p1.sorties, p1.sorties_redfor, p1.sorties_blufor, p1.sorties_underdog) == (3, 1, 1, 1 + 1)
    p2 = Player.objects.get(account_uuid=account(2))
    assert (p2.sorties_redfor, p2.sorties_blufor, p2.sorties_underdog) == (0, 1, 0)
    p3 = Player.objects.get(account_uuid=account(3))
    assert (p3.sorties, p3.sorties_underdog) == (0, 0)  # a gunner counts nowhere


def test_all_time_is_the_sum_of_the_tours_and_a_rebuild_agrees() -> None:
    _first()
    save(
        mission((sortie(0, 1, underdog=True), sortie(1, 2, coalition=2, aircraft_type="F-86A-5", underdog=True))),
        meta("2026-11-20_22-00-00", STARTED_AT + timedelta(days=60)),
    )

    def state() -> list[tuple[object, ...]]:
        return [
            *(tuple(r.values()) for r in Player.objects.order_by("pk").values(*COUNTER_FIELDS)),
            *(tuple(r.values()) for r in PlayerTour.objects.order_by("pk").values(*COUNTER_FIELDS)),
            *(tuple(r.values()) for r in PlayerAircraft.objects.order_by("pk").values(*COUNTER_FIELDS)),
            *(tuple(r.values()) for r in TourAircraftStats.objects.order_by("pk").values(*COUNTER_FIELDS)),
        ]

    incremental = state()
    p1 = Player.objects.get(account_uuid=account(1))
    tours = PlayerTour.objects.filter(player=p1)
    assert tours.count() == 2
    assert p1.sorties_underdog == sum(t.sorties_underdog for t in tours) == 2
    assert p1.sorties_redfor == sum(t.sorties_redfor for t in tours) == 2
    rebuild_aggregates()
    assert state() == incremental


def test_the_aircraft_tour_rows_count_the_sides() -> None:
    _first()
    rows = TourAircraftStats.objects.filter(tour__isnull=False, role="all", mod_pattern="")
    assert sum(r.sorties_redfor for r in rows) == 1
    assert sum(r.sorties_blufor for r in rows) == 1
    assert sum(r.sorties_underdog for r in rows) == 1
