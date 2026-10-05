from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("il2ks_db", "0065_tour_aircraft_stats_sides"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesettings",
            name="rule_settings",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="rule_settings_applied",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
