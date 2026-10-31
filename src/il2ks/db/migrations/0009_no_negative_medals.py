"""Nobody should hunt a negative achievement (doc 17, maintainer 2026-10-31): the hall-of-shame medals and the ram medal
are gone; the hall of shame on the profile stays. Their rows are removed, an admin's choices for them are ignored."""

from typing import Final

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

REMOVED: Final = ("ram", "shame_crashed", "shame_friendly", "shame_strafed", "shame_taxi")


def forwards(apps: Apps, _schema_editor: BaseDatabaseSchemaEditor | None) -> None:
    for model in ("PlayerAchievement", "AchievementHolders"):
        apps.get_model("il2ks_db", model).objects.filter(key__in=REMOVED).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("il2ks_db", "0008_live_delay"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
