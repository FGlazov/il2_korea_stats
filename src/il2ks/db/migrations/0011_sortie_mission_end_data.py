# Data part of the 0011 split: its UPDATEs leave deferred FK trigger events, so no ALTER TABLE may follow in the same
# transaction on Postgres. The constraint part therefore lives in the next migration.

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps
from django.db.models import F


def _retire_mission_ended(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Old rows: `mission_ended` fate/outcome become the new model (doc 13, sortie scope and mission end).

    - `ended_by_mission_end` = the old fate was `mission_ended` (it was set exactly when the sortie was forced).
    - fate `mission_ended` -> `in_aircraft` (same source): the pilot was still in the aircraft.
    - outcome `mission_ended` -> `airborne` when the counts show the aircraft was in the air (air start or takeoffs
      exceed landings); otherwise `unknown`: landed vs ditched needs the landing position, which isn't stored. Run
      `il2ks reprocess` to get the exact value for those.
    """
    sortie = apps.get_model("il2ks_db", "PlayerSortie")
    sortie.objects.filter(pilot_fate="mission_ended").update(ended_by_mission_end=True, pilot_fate="in_aircraft")
    forced = sortie.objects.filter(outcome="mission_ended")
    forced.filter(air_start=True, takeoffs__gte=F("landings")).update(outcome="airborne")
    forced.filter(air_start=False, takeoffs__gt=F("landings")).update(outcome="airborne")
    forced.update(outcome="unknown")


class Migration(migrations.Migration):
    dependencies = [
        ("il2ks_db", "0011_sortie_mission_end_flag_reprocess_requests"),
    ]

    operations = [
        migrations.RunPython(_retire_mission_ended, migrations.RunPython.noop),
    ]
