from django.db import migrations


class Migration(migrations.Migration):
    """No schema change: the catalog now knows second spellings of one object (`object_aliases.csv`, `B 29` -> `B-29`,
    OQ-120). An upgrade merges such rows (`ops/migrate.py`, `BACKFILL_AIRCRAFT_ALIASES`); having a pending migration is
    what triggers that backfill."""

    dependencies = [
        ("il2ks_db", "0048_merge_aircraft_case"),
    ]

    operations: list[migrations.operations.base.Operation] = []
