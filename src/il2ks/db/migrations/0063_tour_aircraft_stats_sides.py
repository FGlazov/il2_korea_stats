from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("il2ks_db", "0062_player_tour_rollup"),
    ]

    operations = [
        migrations.AddField(
            model_name="touraircraftstats",
            name="sorties_redfor",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="touraircraftstats",
            name="sorties_blufor",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
