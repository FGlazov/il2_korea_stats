from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("il2ks_db", "0058_query_plan_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesettings",
            name="level2_pending",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
