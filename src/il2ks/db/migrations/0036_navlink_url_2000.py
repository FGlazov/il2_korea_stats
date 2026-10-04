# Navigation link addresses may be long (OQ-87: a link to a detail page): NavLink.url 300 -> 2000 characters.

from django.db import migrations, models

import il2ks.db.validators


class Migration(migrations.Migration):
    dependencies = [
        ("il2ks_db", "0035_accuracy"),
    ]

    operations = [
        migrations.AlterField(
            model_name="navlink",
            name="url",
            field=models.CharField(max_length=2000, validators=[il2ks.db.validators.validate_http_url]),
        ),
    ]
