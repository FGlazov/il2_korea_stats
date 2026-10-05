"""`ingest.dbutil`: the batched upsert behind `update_rows` writes exactly the named fields, nothing else."""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from il2ks.db.models import Player, PlayerTour, Tour
from il2ks.ingest.dbutil import sync_rows, update_partial_rows, update_rows
from tests.factories import STARTED_AT

pytestmark = pytest.mark.django_db


def make_players(count: int) -> list[Player]:
    return Player.objects.bulk_create(
        [
            Player(
                account_uuid=f"acc-{i}",
                current_name=f"Pilot {i}",
                name_lower=f"pilot {i}",
                first_seen=STARTED_AT,
                last_seen=STARTED_AT,
            )
            for i in range(count)
        ]
    )


def test_update_rows_writes_only_the_named_fields() -> None:
    players = make_players(3)
    for player in players:
        player.sorties = 7
        player.current_name = "Renamed"  # not in `fields`: must not reach the database
    update_rows(Player, players, ["sorties"])
    for stored in Player.objects.all():
        assert stored.sorties == 7
        assert stored.current_name.startswith("Pilot ")


def test_update_rows_keeps_primary_keys_and_row_count() -> None:
    players = make_players(120)  # more than one statement's worth of parameters
    pks = sorted(p.pk for p in players)
    for player in players:
        player.last_seen = STARTED_AT + timedelta(days=1)
        player.kills_air = player.pk
    update_rows(Player, players, ["last_seen", "kills_air"])
    assert sorted(Player.objects.values_list("pk", flat=True)) == pks
    assert all(p.kills_air == p.pk and p.last_seen == STARTED_AT + timedelta(days=1) for p in Player.objects.all())


def test_update_rows_needs_few_statements() -> None:
    players = make_players(60)
    for player in players:
        player.sorties = 1
    with CaptureQueriesContext(connection) as queries:
        update_rows(Player, players, ["sorties"])
    assert len(queries) < len(players) / 4


def test_update_rows_with_nothing_is_a_no_op() -> None:
    with CaptureQueriesContext(connection) as queries:
        update_rows(Player, [], ["sorties"])
    assert len(queries) == 0


def test_update_partial_rows_takes_rows_that_only_carry_their_fields() -> None:
    players = make_players(2)
    update_partial_rows(Player, [Player(pk=p.pk, kills_air=5) for p in players], ["kills_air"])
    for stored in Player.objects.all():
        assert stored.kills_air == 5
        assert stored.current_name.startswith("Pilot ")


def _tour_rows(count: int) -> tuple[Tour, list[Player]]:
    tour = Tour.objects.create(
        title="T", started_at=STARTED_AT, ended_at=STARTED_AT + timedelta(days=30), mode="monthly"
    )
    return tour, make_players(count)


def test_sync_rows_inserts_updates_deletes_and_keeps_primary_keys() -> None:
    """The full-tour refresh syncs rows as value tuples (doc 14 "Level-2 refresh"): a changed row keeps its pk, an
    unchanged one is not written, a missing one is inserted, a row no longer wanted is deleted."""
    tour, players = _tour_rows(4)
    keep, change, drop, add = players
    for player, sorties in ((keep, 1), (change, 2), (drop, 3)):
        PlayerTour.objects.create(player=player, tour=tour, sorties=sorties, kills_air=sorties)
    pks = {r.player_id: r.pk for r in PlayerTour.objects.all()}
    wanted = {
        (keep.pk, tour.pk): {"sorties": 1, "kills_air": 1},
        (change.pk, tour.pk): {"sorties": 5, "kills_air": 2},
        (add.pk, tour.pk): {"sorties": 7, "kills_air": 0},
    }
    sync_rows(
        PlayerTour, PlayerTour.objects.filter(tour=tour), ("player_id", "tour_id"), ("sorties", "kills_air"), wanted
    )
    rows = {r.player_id: r for r in PlayerTour.objects.all()}
    assert set(rows) == {keep.pk, change.pk, add.pk}
    assert [(rows[p.pk].sorties, rows[p.pk].kills_air) for p in (keep, change, add)] == [(1, 1), (5, 2), (7, 0)]
    assert [rows[p.pk].pk for p in (keep, change)] == [pks[keep.pk], pks[change.pk]]  # existing pks kept


def test_sync_rows_reads_without_loading_models_and_writes_nothing_when_equal() -> None:
    """Regression: hydrating every stored row to compare a few numbers was the cost of a full-tour refresh."""
    tour, players = _tour_rows(30)
    PlayerTour.objects.bulk_create([PlayerTour(player=p, tour=tour, sorties=2, kills_air=1) for p in players])
    wanted = {(p.pk, tour.pk): {"sorties": 2, "kills_air": 1} for p in players}
    with CaptureQueriesContext(connection) as queries:
        sync_rows(
            PlayerTour, PlayerTour.objects.filter(tour=tour), ("player_id", "tour_id"), ("sorties", "kills_air"), wanted
        )
    statements = [q["sql"].split()[0] for q in queries]
    assert "INSERT" not in statements
    assert "UPDATE" not in statements
    assert len(queries) <= 2  # one read, one (empty-set) delete at most
    assert "kills_ground" not in queries[0]["sql"]  # the key and two counters, not the whole row


SQLITE_VARIABLE_LIMIT = 32766


@pytest.mark.xfail(strict=True, reason="unbatched stale delete")
def test_sync_rows_deletes_more_stale_rows_than_sqlite_has_variables() -> None:
    """A shrinking big tour (`rebuild-aggregates --retour`) leaves tens of thousands of stale rows: one
    `pk__in=[...]` delete would hit "too many SQL variables"."""
    tour, players = _tour_rows(SQLITE_VARIABLE_LIMIT + 300)
    PlayerTour.objects.bulk_create([PlayerTour(player=p, tour=tour, sorties=1) for p in players])
    sync_rows(PlayerTour, PlayerTour.objects.filter(tour=tour), ("player_id", "tour_id"), ("sorties",), {})
    assert not PlayerTour.objects.exists()


@pytest.mark.xfail(strict=True, reason="unbatched stale delete")
def test_sync_best_deletes_more_stale_rows_than_sqlite_has_variables() -> None:
    from il2ks.db.models import PlayerBestStreak, StreakKind, StreakTrack
    from il2ks.ingest.streaks import _sync_best

    tour, players = _tour_rows(6000)
    kinds = list(StreakKind.values)
    tracks = list(StreakTrack.values)
    assert 6000 * len(kinds) * len(tracks) > SQLITE_VARIABLE_LIMIT
    PlayerBestStreak.objects.bulk_create(
        [
            PlayerBestStreak(player=p, tour=tour, track=track, kind=kind, since=STARTED_AT, until=STARTED_AT)
            for p in players
            for track in tracks
            for kind in kinds
        ]
    )
    _sync_best([p.pk for p in players], None, {}, all_time=False)
    assert not PlayerBestStreak.objects.exists()
