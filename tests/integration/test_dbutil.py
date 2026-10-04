"""`ingest.dbutil`: the batched upsert behind `update_rows` writes exactly the named fields, nothing else."""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from il2ks.db.models import Player
from il2ks.ingest.dbutil import update_partial_rows, update_rows
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
