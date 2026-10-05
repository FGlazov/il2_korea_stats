"""One level-2 path, three ways in (doc 14 "Level-2 refresh"): missions of three tours, a late import into an old tour
and a re-ingest that moves a mission to another tour, saved one by one (`save_mission`), as one batch (`save_level1` +
`Level2Batch`) and then rebuilt (`rebuild_aggregates`) must leave identical databases, including the per-tour Elo, the
aircraft type Elo (per tour and all time), the streaks and the medals with their all-time roll-ups (tests.db_canon:
primary-key free, sorted, floats rounded)."""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from django.db import transaction

from il2ks.core.ratings.elo import DEFAULT_RULES
from il2ks.core.ratings.score import DEFAULT_SCORE_RULES
from il2ks.core.replay.result import MissionResult
from il2ks.core.stat_marks import DEFAULT_MARK_RULES
from il2ks.core.tours import DEFAULT_TOUR_RULES
from il2ks.db.models import (
    AircraftStats,
    PlayerAchievement,
    PlayerBestStreak,
    PlayerSortie,
    PlayerTourPool,
    Tour,
    TourAircraftStats,
)
from il2ks.ingest import persist
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.batch import Level2Batch
from tests.db_canon import canonical_dump, diff_dumps
from tests.factories import FakeCatalog, kill, meta, mission, save, sortie

pytestmark = pytest.mark.django_db

AIR = "air_superiority"
JET = {1: "MiG-15bis", 2: "F-86A-5", 3: "MiG-15bis", 4: "F-86A-5"}
SIDE = {1: 1, 2: 2, 3: 1, 4: 2}  # odd players fly for the other side than even ones: every win is against an enemy
type Dump = dict[str, list[str]]


def duels(wins: list[tuple[int, int]], survivors: tuple[int, ...] = ()) -> MissionResult:
    """A mission of jet pilots; `wins` = (winner, loser) player numbers in kill order, `survivors` fly kill-less."""
    players = sorted({p for pair in wins for p in pair} | set(survivors))
    index = {p: i for i, p in enumerate(players)}
    won = {p: sum(1 for w, _ in wins if w == p) for p in players}
    lost = {p for _, p in wins}
    return mission(
        tuple(
            sortie(
                index[p],
                p,
                aircraft_type=JET[p],
                coalition=SIDE[p],
                combat_role=AIR,
                kills_air=won[p],
                kills_air_pvp=won[p],
                is_death=p in lost,
                outcome="shot_down" if p in lost else "landed",
            )
            for p in players
        ),
        tuple(kill(100 * (n + 1), index[w], index[v]) for n, (w, v) in enumerate(wins)),
    )


@dataclass(frozen=True)
class Step:
    uid: str
    started: datetime
    result: MissionResult


def at(month: int, day: int) -> datetime:
    return datetime(2026, month, day, 20, 0, tzinfo=UTC)


STEPS = [
    Step("a", at(9, 19), duels([(1, 2), (2, 3)])),
    Step("b", at(10, 5), duels([(2, 1), (4, 1), (4, 3)])),
    Step("c", at(11, 20), duels([(3, 4), (1, 4), (1, 2)], survivors=(2,))),
    Step("e", at(9, 28), duels([(1, 3), (3, 2)])),  # September, first...
    Step("d", at(9, 25), duels([(3, 1), (3, 4)])),  # ... and a late import into September
    Step("e", at(10, 12), duels([(1, 3), (3, 2)])),  # the re-ingest of "e": it moves to October
]


class _Rollback(Exception):
    pass


@contextmanager
def scratch() -> Generator[None]:
    """Whatever happens inside is rolled back afterwards (one test compares several ways of reaching the state)."""
    try:
        with transaction.atomic():
            yield
            raise _Rollback
    except _Rollback:
        pass


def single_path() -> None:
    for step in STEPS:
        save(step.result, meta(step.uid, step.started))


def batched_path() -> None:
    batch = Level2Batch(len(STEPS), DEFAULT_RULES, DEFAULT_MARK_RULES)
    batch.start()
    for step in STEPS:
        with transaction.atomic():
            _, tours = persist.save_level1(
                step.result, meta(step.uid, step.started), FakeCatalog(), DEFAULT_TOUR_RULES, DEFAULT_SCORE_RULES
            )
            batch.add(tours)
        batch.mission_done()
    batch.finish()


def dumps(ingest: Callable[[], None]) -> tuple[Dump, Dump]:
    """The database after `ingest`, and after a full rebuild on top of it."""
    with scratch():
        ingest()
        ingested = canonical_dump()
        rebuild_aggregates(DEFAULT_RULES, marks=DEFAULT_MARK_RULES)
        rebuilt = canonical_dump()
    return ingested, rebuilt


def test_single_batched_and_rebuilt_level_2_are_identical() -> None:
    single, single_rebuilt = dumps(single_path)
    batched, batched_rebuilt = dumps(batched_path)

    assert diff_dumps(single, single_rebuilt) == []
    assert diff_dumps(batched, batched_rebuilt) == []
    assert diff_dumps(single, batched) == []


def test_the_scenario_has_something_in_every_part_it_compares() -> None:
    with scratch():
        single_path()
        assert Tour.objects.count() == 3
        assert PlayerTourPool.objects.filter(elo_games__gt=0).count() >= 3  # an Elo per tour
        assert PlayerSortie.objects.filter(elo_peak__gt=0).exists()
        assert TourAircraftStats.objects.filter(tour__isnull=False, elo_games__gt=0).count() >= 3  # a type Elo per tour
        assert AircraftStats.objects.filter(elo_games__gt=0).exists()  # and its all-time roll-up
        assert PlayerBestStreak.objects.filter(tour__isnull=False).exists()
        assert PlayerBestStreak.objects.filter(tour__isnull=True).exists()  # the all-time roll-up
        assert PlayerAchievement.objects.filter(tour__isnull=False).exists()
        assert PlayerAchievement.objects.filter(tour__isnull=True).exists()
        assert PlayerAchievement.objects.filter(key="elo_peak").exists()  # a medal that reads the Elo replay
