from django.db import migrations


class Migration(migrations.Migration):
    """No schema change: the loadout names came from a new catalog table (F-86A-5 and IL-10 payloads renamed, many
    added), so an upgrade re-derives the stored `payload_name` of every sortie from its stored `payload_id`
    (`ops/migrate.py`, `BACKFILL_PAYLOAD_NAMES`). Having a pending migration is what triggers that backfill."""

    dependencies = [
        ("il2ks_db", "0044_sortie_achievement_facts"),
    ]

    operations: list[migrations.operations.base.Operation] = []
