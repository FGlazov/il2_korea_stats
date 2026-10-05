"""The all-time medal roll-up reads only what can change (doc 14 "Level-2 refresh", maintainer rule: a refresh reads
only its tours' level 1; all time is a roll-up of tour rows).

A career tier is earned in the first tour whose running total crosses it, so the tours older than the oldest touched
tour cannot change: their all-time rows are kept and never replayed. Only the (player, tour) pairs that can change are
replayed, one query per tour, never a cross product of every player and every tour any tier was ever crossed in."""

import re
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from il2ks.core.replay.result import MissionResult
from il2ks.db.models import PlayerAchievement, Tour
from il2ks.ingest.aggregates import rebuild_aggregates, refresh_tours
from tests.factories import STARTED_AT, meta, mission, save, sortie

pytestmark = pytest.mark.django_db

OCTOBER = STARTED_AT + timedelta(days=40)
NOVEMBER = STARTED_AT + timedelta(days=72)
DECEMBER = STARTED_AT + timedelta(days=105)
TOUR_FILTER = re.compile(r'"tour_id" (?:in \(([\d, ]+)\)|= (\d+))')


def snapshot() -> list[tuple[object, ...]]:
    return list(
        PlayerAchievement.objects.order_by("player_id", "tour_id", "key", "tier").values_list(
            "player_id", "tour_id", "key", "tier", "earned_at", "sortie_id", "mission_id"
        )
    )


def tours_flown(kills_one: tuple[int, int], kills_two: int = 0) -> MissionResult:
    """A mission with two sorties of pilot 1 (`kills_one` air kills each) and, optionally, one of pilot 2."""
    sorties = [sortie(0, 1, kills_air=kills_one[0]), sortie(1, 1, kills_air=kills_one[1])]
    if kills_two:
        sorties.append(sortie(2, 2, kills_air=kills_two))
    return mission(tuple(sorties))


# Career kills tiers all time: 5, 50, 250, 1250. Pilot 1: tier 1 in October (3 + 4), tier 2 in November (7 + 45).
STEPS = [
    ("september", lambda: save(tours_flown((2, 1), 1), meta("2026-09-19_22-34-13", STARTED_AT))),
    ("october", lambda: save(tours_flown((2, 2), 6), meta("2026-10-29_22-00-00", OCTOBER))),
    ("november", lambda: save(tours_flown((20, 25), 2), meta("2026-11-30_21-00-00", NOVEMBER))),
    ("december", lambda: save(tours_flown((1, 1), 1), meta("2026-12-31_21-00-00", DECEMBER))),
    ("late import into september", lambda: save(tours_flown((2, 3), 0), meta("2026-09-25_20-00-00", STARTED_AT))),
    ("late import into november", lambda: save(tours_flown((30, 0), 4), meta("2026-11-12_20-00-00", NOVEMBER))),
]


def test_incremental_equals_rebuild_with_career_tiers_crossed_in_several_tours() -> None:
    crossed: set[int | None] = set()
    for label, step in STEPS:
        step()
        incremental = snapshot()
        rebuild_aggregates()
        assert snapshot() == incremental, label
        crossed |= {
            tour_id
            for (tour_id,) in PlayerAchievement.objects.filter(key="career_kills", tour__isnull=True)
            .values_list("sortie__mission__tour_id")
            .distinct()
        }
    assert len(crossed) >= 3, "the career tiers are earned in too few tours: the comparison would prove little"


def level_one_tour_filters(queries: CaptureQueriesContext) -> list[set[int]]:
    """The tours each query on the sorties or kills is restricted to (an unrestricted query gives an empty set). The
    activity days' query (restricted by the missions' start time, not their tour) is not one of them."""
    found: list[set[int]] = []
    for q in queries:
        sql = q["sql"].lower()
        if ('from "il2ks_db_playersortie"' in sql or 'from "il2ks_db_kill"' in sql) and '"started_at" >=' not in sql:
            tours: set[int] = set()
            for many, one in TOUR_FILTER.findall(sql):
                tours |= {int(n) for n in re.findall(r"\d+", many or one)}
            found.append(tours)
    return found


def test_a_refresh_of_the_newest_tour_reads_only_its_sorties() -> None:
    for _, step in STEPS[:4]:
        step()
    newest = Tour.objects.order_by("started_at").last()
    assert newest is not None
    with CaptureQueriesContext(connection) as queries:
        refresh_tours([newest.pk], None, payload_elo=False)  # the payload Elo reads more: not this roll-up
    filters = level_one_tour_filters(queries)
    assert filters, "no level-1 query was captured"
    for tours in filters:
        assert tours == {newest.pk}, "a refresh of the newest tour read the sorties of other tours (or all of them)"


def test_a_late_import_replays_only_the_tours_from_the_oldest_touched_one() -> None:
    for _, step in STEPS[:4]:
        step()
    _september, _october, november, december = Tour.objects.order_by("started_at")
    with CaptureQueriesContext(connection) as queries:
        refresh_tours([november.pk], None, payload_elo=False)
    allowed = {november.pk, december.pk}  # a later tour's carried-in total changes with the touched one
    for tours in level_one_tour_filters(queries):
        assert tours <= allowed, f"tours before the touched one were read: {tours - allowed}"
