"""Migration 0010: old `mission_ended` outcomes and fates become the airborne / in_aircraft + flag model (doc 13)."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from il2ks.db.models import PlayerSortie
from tests.factories import mission, save, sortie

BEFORE = [("il2ks_db", "0009_ground_kill_categories")]
AFTER = [("il2ks_db", "0010_sortie_ended_by_mission_end")]


@pytest.mark.django_db(transaction=True)
def test_old_mission_ended_rows_are_mapped() -> None:
    # Five sorties saved at the current schema, then rolled back to 0009 and rewritten the way the old code stored them.
    save(mission(tuple(sortie(i, i + 1) for i in range(5))))
    pks = list(PlayerSortie.objects.order_by("spawn_tick").values_list("pk", flat=True))
    executor = MigrationExecutor(connection)
    try:
        executor.migrate(BEFORE)
        old = executor.loader.project_state(BEFORE).apps.get_model("il2ks_db", "PlayerSortie")
        # (took off, takeoffs, landings, air_start, outcome, fate) as the old code wrote a forced sortie
        rows = [
            (1, 1, 0, False, "mission_ended", "mission_ended"),  # still in the air
            (2, 1, 1, False, "mission_ended", "mission_ended"),  # on the ground: landed or ditched, not stored
            (3, 0, 0, True, "mission_ended", "mission_ended"),  # air start, never landed
            (4, 0, 0, False, "not_taken_off", "mission_ended"),  # never took off
            (0, 1, 1, False, "landed", "in_aircraft"),  # not forced
        ]
        for pk, (_, takeoffs, landings, air_start, outcome, fate) in zip(pks, rows, strict=True):
            old.objects.filter(pk=pk).update(
                takeoffs=takeoffs, landings=landings, air_start=air_start, outcome=outcome, pilot_fate=fate
            )

        executor = MigrationExecutor(connection)
        executor.migrate(AFTER)
        new = executor.loader.project_state(AFTER).apps.get_model("il2ks_db", "PlayerSortie")
        got = [
            (r.outcome, r.pilot_fate, r.ended_by_mission_end)
            for r in new.objects.filter(pk__in=pks).order_by("spawn_tick")
        ]
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
