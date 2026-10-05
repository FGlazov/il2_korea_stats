"""The damage a sortie carried into a landing (maintainer 2026-10-05, doc 13 "Damage per flight leg").

`damage_taken` is the last flight leg's damage, so a landing that was repaired reads "unharmed" there. `landing_damage`
is the most damage carried into any landing, for the damaged-landing medal and the "limped home" quips.

Backfill: a sortie that ended in a landing gets `damage_taken` (exact unless the sortie also had a repaired landing
earlier: `il2ks reprocess --all` fills in those). The medals are recomputed by the next level-2 rebuild.
"""

from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def backfill(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    sortie = apps.get_model("il2ks_db", "PlayerSortie")
    sortie.objects.filter(outcome="landed").update(landing_damage=models.F("damage_taken"))


class Migration(migrations.Migration):
    dependencies = [
        ("il2ks_db", "0071_stat_threshold_top_tiers"),
    ]

    operations = [
        migrations.AddField(
            model_name="playersortie",
            name="landing_damage",
            field=models.FloatField(default=0.0),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
