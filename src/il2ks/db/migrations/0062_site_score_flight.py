from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("il2ks_db", "0061_tour_clean_slate_streak"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesettings",
            name="score_flight",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="sitesettings",
            name="score_flight_applied",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
