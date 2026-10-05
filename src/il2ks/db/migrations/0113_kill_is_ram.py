"""`Kill.is_ram`: the credit came from a mid-air collision (`[rules] credit_rams`), shown on the sortie page.

No backfill: the flag (and the `ram` key of the stored timeline rows) is filled by `il2ks reprocess --all`."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("il2ks_db", "0072_sortie_landing_damage"),
    ]

    operations = [
        migrations.AddField(
            model_name="kill",
            name="is_ram",
            field=models.BooleanField(default=False),
        ),
    ]
