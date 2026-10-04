from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def delete_extra_kinds(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """OQ-117: the profile shows the favourite loadout only, so the weapon-mod and gun-ammo tallies go (a separate
    migration from the schema change that follows: Postgres refuses to alter a table with pending trigger events)."""
    apps.get_model("il2ks_db", "PlayerAircraftBuild").objects.exclude(kind="payload").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("il2ks_db", "0051_mod_filters"),
    ]

    operations = [migrations.RunPython(delete_extra_kinds, migrations.RunPython.noop)]
