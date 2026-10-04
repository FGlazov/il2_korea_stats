"""Pilot damage per sortie (OQ-115) and aircraft damage 1.0 when destroyed.

Backfill from stored data: a destroyed aircraft gets damage_taken 1.0 and a dead pilot pilot_damage 1.0. The pilot's
damage of the other sorties is not stored anywhere (NULL = unknown, no health shown): `il2ks reprocess --all` fills it.
"""

from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def backfill(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    sortie = apps.get_model("il2ks_db", "PlayerSortie")
    sortie.objects.filter(aircraft_status="destroyed").update(damage_taken=1.0)
    sortie.objects.filter(models.Q(is_death=True) | models.Q(pilot_status="dead")).update(pilot_damage=1.0)


class Migration(migrations.Migration):
    dependencies = [
        ("il2ks_db", "0054_live_sorties"),
    ]

    operations = [
        migrations.AddField(
            model_name="playersortie",
            name="pilot_damage",
            field=models.FloatField(default=None, null=True),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
