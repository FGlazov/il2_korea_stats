"""Migration 0010: old `mission_ended` outcomes and fates become the airborne / in_aircraft + flag model (doc 13)."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from il2ks.db.models import PlayerSortie
from tests.factories import mission, save, sortie

BEFORE = ("il2ks_db", "0010_pve_breakdown")
AFTER = ("il2ks_db", "0011_sortie_mission_end_constraints")
TABLE = PlayerSortie._meta.db_table


@pytest.mark.django_db(transaction=True)
def test_old_mission_ended_rows_are_mapped() -> None:
    # Five sorties saved at the current schema, then rolled back to 0009 and rewritten the way the old code stored them.
    save(mission(tuple(sortie(i, i + 1) for i in range(5))))
    pks = list(PlayerSortie.objects.order_by("spawn_tick").values_list("pk", flat=True))
    try:
        MigrationExecutor(connection).migrate([BEFORE])
        # (takeoffs, landings, air_start, outcome, fate) as the old code wrote a forced sortie
        rows = [
            (1, 0, False, "mission_ended", "mission_ended"),  # still in the air
            (1, 1, False, "mission_ended", "mission_ended"),  # on the ground: landed or ditched, not stored
            (0, 0, True, "mission_ended", "mission_ended"),  # air start, never landed
            (0, 0, False, "not_taken_off", "mission_ended"),  # never took off
            (1, 1, False, "landed", "in_aircraft"),  # not forced
        ]
        with connection.cursor() as cursor:
            for pk, row in zip(pks, rows, strict=True):
                cursor.execute(
                    f"UPDATE {TABLE} SET takeoffs=%s, landings=%s, air_start=%s, outcome=%s, pilot_fate=%s WHERE id=%s",
                    [*row, pk],
                )
        MigrationExecutor(connection).migrate([AFTER])
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT outcome, pilot_fate, ended_by_mission_end FROM {TABLE} ORDER BY spawn_tick")
            got = [(outcome, fate, bool(flag)) for outcome, fate, flag in cursor.fetchall()]
    finally:
        final = MigrationExecutor(connection)
        final.migrate(final.loader.graph.leaf_nodes())
    assert got == [
        ("airborne", "in_aircraft", True),
        ("unknown", "in_aircraft", True),
        ("airborne", "in_aircraft", True),
        ("not_taken_off", "in_aircraft", True),
        ("landed", "in_aircraft", False),
    ]
