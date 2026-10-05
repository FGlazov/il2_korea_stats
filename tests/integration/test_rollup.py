"""The generic roll-up helper (`ingest.rollup`, doc 14): all-time rows = SUM / MAX / MIN of per-tour rows."""

from datetime import UTC, datetime, timedelta

import pytest

from il2ks.db.models import Player, PlayerPool, PlayerTour, PlayerTourPool, Tour
from il2ks.ingest.rollup import rollup

pytestmark = pytest.mark.django_db

T0 = datetime(2026, 9, 1, tzinfo=UTC)


def _player(n: int) -> Player:
    return Player.objects.create(
        account_uuid=f"acc-{n}", current_name=f"P{n}", name_lower=f"p{n}", first_seen=T0, last_seen=T0
    )


def _tour(n: int) -> Tour:
    start = T0 + timedelta(days=30 * n)
    return Tour.objects.create(title=f"Tour {n}", started_at=start, ended_at=start + timedelta(days=30), mode="monthly")


def _pool_rollup(players: list[int]) -> None:
    rollup(
        PlayerPool,
        PlayerPool.objects.filter(player_id__in=players),
        PlayerTourPool.objects.filter(player_id__in=players),
        key=("player_id", "propulsion"),
        sums=("sorties", "kills_air", "flight_time_s"),
    )


def test_sums_the_tour_rows_creates_updates_and_deletes() -> None:
    a, b = _player(1), _player(2)
    t1, t2 = _tour(1), _tour(2)
    PlayerTourPool.objects.create(player=a, tour=t1, propulsion="prop", sorties=2, kills_air=3, flight_time_s=0.1)
    PlayerTourPool.objects.create(player=a, tour=t2, propulsion="prop", sorties=1, kills_air=1, flight_time_s=0.2)
    PlayerTourPool.objects.create(player=a, tour=t2, propulsion="jet", sorties=4, kills_air=0, flight_time_s=5.0)
    PlayerPool.objects.create(player=b, propulsion="jet", sorties=9)  # no tour row: goes away

    _pool_rollup([a.pk, b.pk])

    rows = {(r.player_id, r.propulsion): r for r in PlayerPool.objects.all()}
    assert set(rows) == {(a.pk, "prop"), (a.pk, "jet")}
    assert (rows[(a.pk, "prop")].sorties, rows[(a.pk, "prop")].kills_air) == (3, 4)
    assert rows[(a.pk, "prop")].flight_time_s == 0.3  # 0.1 + 0.2 is 0.30000000000000004 unrounded

    pk = rows[(a.pk, "prop")].pk
    PlayerTourPool.objects.filter(player=a, tour=t2, propulsion="prop").update(sorties=5)
    _pool_rollup([a.pk])
    assert PlayerPool.objects.get(pk=pk).sorties == 7  # updated in place, the primary key kept


def test_update_only_zeroes_an_entity_without_tour_rows() -> None:
    a, b = _player(1), _player(2)
    t1 = _tour(1)
    PlayerTour.objects.create(player=a, tour=t1, sorties=3, kills_air=2)
    Player.objects.filter(pk=b.pk).update(sorties=8)

    rollup(
        Player,
        Player.objects.filter(pk__in=[a.pk, b.pk]),
        PlayerTour.objects.filter(player_id__in=[a.pk, b.pk]),
        key=("player_id",),
        model_key=("pk",),
        sums=("sorties", "kills_air"),
        update_only=True,
    )

    a.refresh_from_db()
    b.refresh_from_db()
    assert (a.sorties, a.kills_air) == (3, 2)
    assert (b.sorties, b.kills_air) == (0, 0)  # the identity stays, its counters are zero


def test_a_derived_value_follows_the_tour_rows() -> None:
    a = _player(1)
    t1, t2 = _tour(1), _tour(2)
    PlayerTourPool.objects.create(player=a, tour=t1, propulsion="prop", sorties=1)
    PlayerTourPool.objects.create(player=a, tour=t2, propulsion="prop", sorties=2)

    rollup(
        PlayerPool,
        PlayerPool.objects.filter(player=a),
        PlayerTourPool.objects.filter(player=a),
        key=("player_id", "propulsion"),
        sums=("sorties",),
        derived={"kills_air": lambda rows: max(int(str(r["sorties"])) for r in rows) * 10},
    )

    assert PlayerPool.objects.get(player=a).kills_air == 20
