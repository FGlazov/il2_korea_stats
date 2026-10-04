from django.db import migrations


class Migration(migrations.Migration):
    """No schema change: one aircraft type written in two cases by the logs (`Il-10` / `IL-10`) had two `GameObject`
    rows. An upgrade merges them (`ops/migrate.py`, `BACKFILL_AIRCRAFT_CASE`); having a pending migration is what
    triggers that backfill."""

    dependencies = [
        ("il2ks_db", "0047_home_feature_image"),
    ]

    operations: list[migrations.operations.base.Operation] = []
