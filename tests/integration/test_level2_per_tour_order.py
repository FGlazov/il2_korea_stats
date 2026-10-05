"""The per-tour order of level 2 (doc 14 "Level-2 refresh"): for the tours a save touched, the rows from level 1,
then that tour's Elo replay (it writes `PlayerSortie.elo_peak`), then that tour's streaks and medals (the Top Rated
medal reads the peaks), then the all-time roll-ups (streaks, medals, Elo maximum), then the holder counts and the stat
thresholds. Elo, streaks and medals each start over in a tour (OQ-128, doc 17), so only the touched tours are replayed
or refreshed, never every tour."""

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import cast

import pytest

from il2ks.core.ratings.elo import DEFAULT_RULES
from il2ks.db.models import Tour
from il2ks.ingest import aggregates, persist
from tests.factories import kill, meta, mission, save, sortie

pytestmark = pytest.mark.django_db

AIR = "air_superiority"
SEPTEMBER = datetime(2026, 9, 19, 20, 0, tzinfo=UTC)
LATER_SEPTEMBER = datetime(2026, 9, 25, 20, 0, tzinfo=UTC)
OCTOBER = datetime(2026, 10, 5, 20, 0, tzinfo=UTC)


def duel(uid: str, when: datetime) -> None:
    """Player 1 shoots down player 2 (one PvP air kill between air superiority sorties)."""
    sorties = (
        sortie(0, 1, aircraft_type="MiG-15bis", coalition=1, combat_role=AIR, kills_air=1, kills_air_pvp=1),
        sortie(1, 2, aircraft_type="F-86A-5", coalition=2, combat_role=AIR, is_death=True, outcome="shot_down"),
    )
    save(mission(sorties, (kill(100, 0, 1),)), meta(uid, when))


def tour_at(moment: datetime) -> Tour:
    return Tour.objects.get(started_at__lte=moment, ended_at__gt=moment)


class Log:
    """The calls of the level-2 steps, in order, as `name` or `(name, tour ids)`; consecutive repeats collapse (a step
    that works in chunks of players is one step)."""

    def __init__(self) -> None:
        self.calls: list[object] = []

    def add(self, call: object) -> None:
        if not self.calls or self.calls[-1] != call:
            self.calls.append(call)


def _spy(
    monkeypatch: pytest.MonkeyPatch, module: object, name: str, log: Log, tours: Callable[..., object] | None = None
) -> None:
    original: Callable[..., object] = getattr(module, name)

    def spy(*args: object, **kwargs: object) -> object:
        log.add(name if tours is None else (name, tours(*args, **kwargs)))
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, spy)


def _tour_ids_of_ratings(*args: object, **kwargs: object) -> list[int] | None:
    ids = kwargs.get("tour_ids", args[1] if len(args) > 1 else None)
    return None if ids is None else sorted(cast("Iterable[int]", ids))


def _watch(monkeypatch: pytest.MonkeyPatch) -> Log:
    log = Log()
    _spy(monkeypatch, aggregates, "recompute_player_rows", log)
    _spy(monkeypatch, aggregates, "recompute_ratings", log, _tour_ids_of_ratings)
    _spy(monkeypatch, aggregates, "refresh_streak_tours", log)
    _spy(monkeypatch, aggregates, "refresh_achievement_tours", log)
    _spy(monkeypatch, aggregates, "rollup_streaks", log)
    _spy(monkeypatch, aggregates, "rollup_achievements", log)
    _spy(monkeypatch, persist, "recompute_holders", log)
    _spy(monkeypatch, persist, "recompute_thresholds", log)
    return log


def test_a_save_refreshes_its_tour_in_the_documented_order(monkeypatch: pytest.MonkeyPatch) -> None:
    duel("m1", SEPTEMBER)
    september = tour_at(SEPTEMBER).pk
    log = _watch(monkeypatch)

    duel("m2", OCTOBER)

    october = tour_at(OCTOBER).pk
    assert october != september
    assert log.calls == [
        "recompute_player_rows",
        ("recompute_ratings", [october]),  # only the touched tour is replayed
        "refresh_streak_tours",  # after the replay: the Top Rated medal reads the peaks
        "refresh_achievement_tours",
        "rollup_streaks",
        "rollup_achievements",
        "recompute_holders",
        "recompute_thresholds",
    ]


def test_a_late_import_replays_only_the_old_tour(monkeypatch: pytest.MonkeyPatch) -> None:
    duel("m1", SEPTEMBER)
    duel("m2", OCTOBER)
    log = _watch(monkeypatch)

    duel("m3", LATER_SEPTEMBER)  # belongs to the September tour again

    assert ("recompute_ratings", [tour_at(SEPTEMBER).pk]) in log.calls
    assert [c for c in log.calls if isinstance(c, tuple)] == [("recompute_ratings", [tour_at(SEPTEMBER).pk])]


def test_a_reingest_that_moves_a_mission_replays_both_tours(monkeypatch: pytest.MonkeyPatch) -> None:
    duel("m1", SEPTEMBER)
    duel("m2", OCTOBER)
    log = _watch(monkeypatch)

    duel("m1", OCTOBER)  # the same mission, now starting in October: it leaves September

    both = sorted([tour_at(SEPTEMBER).pk, tour_at(OCTOBER).pk])
    assert [c for c in log.calls if isinstance(c, tuple)] == [("recompute_ratings", both)]


def test_the_rating_replay_no_longer_reruns_the_medals_by_itself() -> None:
    """The medals run once per refresh, after the replay: the replay has no medal call of its own to repeat them."""
    from il2ks.ingest import ratings

    assert not hasattr(ratings, "recompute_achievements")


def test_the_default_rules_still_replay_when_called_directly() -> None:
    """`recompute_ratings(rules, tour_ids)` keeps its two parameters, so a caller can name the tours."""
    duel("m1", SEPTEMBER)
    from il2ks.ingest.ratings import recompute_ratings

    assert recompute_ratings(DEFAULT_RULES, [tour_at(SEPTEMBER).pk]) == 1
