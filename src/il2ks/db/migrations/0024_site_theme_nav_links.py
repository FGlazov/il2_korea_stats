# Branding: custom navigation links (ordered `NavLink` rows), per-token colour themes, font choices (TD-25).
# The single accent colour moves into the theme (both modes); the old "Links" list becomes NavLink rows (same order,
# no icon). The RunPython inserts rows that reference `SiteSettings`, which leaves deferred FK trigger events, and
# Postgres refuses the later ALTER TABLE on that table in the same transaction ("pending trigger events"), so the data
# step ends with SET CONSTRAINTS ALL IMMEDIATE on Postgres (same as 0011).

import django.db.models.deletion
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

import il2ks.db.validators


def _move_branding(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    site_model = apps.get_model("il2ks_db", "SiteSettings")
    link_model = apps.get_model("il2ks_db", "NavLink")
    for site in site_model.objects.all():
        raw = site.links if isinstance(site.links, list) else []
        kept: list[dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            label, url = str(item.get("label", "")).strip()[:60], str(item.get("url", "")).strip()
            if label and url.lower().startswith(("http://", "https://")) and len(url) <= 300 and " " not in url:
                kept.append({"label": label, "url": url, "icon": ""})
        for position, link in enumerate(kept, start=1):
            link_model.objects.create(site=site, label=link["label"], url=link["url"], icon="", position=position)
        site.links = kept
        accent = site.accent_color.strip().upper()
        if len(accent) == 7 and accent.startswith("#") and all(c in "0123456789ABCDEF" for c in accent[1:]):
            site.theme = {"light": {"accent": accent}, "dark": {"accent": accent}}
        site.save(update_fields=["links", "theme"])
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")  # the only raw SQL here: fire the deferred FK checks now


class Migration(migrations.Migration):
    dependencies = [
        ("il2ks_db", "0023_friendly_fire_incidents"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesettings",
            name="theme",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="heading_font",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="body_font",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.CreateModel(
            name="NavLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("label", models.CharField(max_length=60)),
                ("url", models.CharField(max_length=300, validators=[il2ks.db.validators.validate_http_url])),
                (
                    "icon",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("", "No icon"),
                            ("discord", "Discord"),
                            ("forum", "Forum"),
                            ("patreon", "Patreon"),
                            ("link", "Generic link"),
                        ],
                        default="",
                        max_length=10,
                    ),
                ),
                ("position", models.PositiveSmallIntegerField(default=0)),
                (
                    "site",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="nav_links",
                        to="il2ks_db.sitesettings",
                    ),
                ),
            ],
            options={
                "ordering": ["position", "id"],
            },
        ),
        migrations.RunPython(_move_branding, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="sitesettings",
            name="accent_color",
        ),
    ]
