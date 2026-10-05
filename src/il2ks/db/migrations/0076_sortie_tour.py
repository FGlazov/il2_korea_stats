"""The tour on every sortie (maintainer 2026-10-05): `PlayerSortie.tour`, always the mission's tour.

The site-wide sortie list (`/sorties/`, the current tour by default) sorts a tour's sorties newest first; the tour
lived on the mission row only, so the sort could not use an index. The composite index `sortie_tour_recent` serves the
page and its COUNT. Every write of `Mission.tour` keeps the column in step (design_doc/14).

Backfill: one UPDATE ... SET tour = (SELECT tour FROM mission). The index is created before the backfill: on Postgres
an ALTER after the UPDATE of a foreign key fails on the pending deferred trigger events.
"""

from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def backfill(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    sortie = apps.get_model("il2ks_db", "PlayerSortie")
    mission = apps.get_model("il2ks_db", "Mission")
    sortie.objects.filter(tour__isnull=True).update(
        tour_id=models.Subquery(mission.objects.filter(pk=models.OuterRef("mission_id")).values("tour_id")[:1])
    )


class Migration(migrations.Migration):
    dependencies = [
        ("il2ks_db", "0075_branding_backgrounds_favicon"),
    ]

    operations = [
        migrations.AddField(
            model_name="playersortie",
            name="tour",
            field=models.ForeignKey(
                db_index=False,
                null=True,
                on_delete=models.deletion.PROTECT,
                related_name="sortie_rows",
                to="il2ks_db.tour",
            ),
        ),
        migrations.AddIndex(
            model_name="playersortie",
            index=models.Index(
                fields=["tour", "-spawned_at", "-id", "role", "mission", "player"], name="sortie_tour_recent"
            ),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
