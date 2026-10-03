from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def flag_existing_overrides(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Names that differ from the shipped default (and from the raw log name an unknown type starts with) were typed
    by an admin: mark them, so a catalog update refreshes every other name but keeps these (TD-24)."""
    from il2ks.core.catalog.loader import load_default_catalog

    catalog = load_default_catalog()
    game_object = apps.get_model("il2ks_db", "GameObject")
    alias = schema_editor.connection.alias
    for obj in game_object.objects.using(alias).all():
        default = catalog.lookup(obj.log_name).display_name or obj.log_name
        if obj.display_name not in {default, obj.log_name}:
            obj.name_overridden = True
            obj.save(update_fields=["name_overridden"])


class Migration(migrations.Migration):
    dependencies = [
        ("il2ks_db", "0013_tour_counters_pve"),
    ]

    operations = [
        migrations.AddField(
            model_name="gameobject",
            name="name_overridden",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(flag_existing_overrides, migrations.RunPython.noop),
    ]
