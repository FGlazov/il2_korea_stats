"""`il2ks db copy`: copy every il2ks table between database aliases, keeping primary keys (TD-19).

This is the whole SQLite -> Postgres migration path, so it's covered by the transfer test.
"""

from django.apps import apps
from django.core.management.color import no_style
from django.db import connections, models, transaction

BATCH_SIZE = 1000


def _app_models() -> list[type[models.Model]]:
    """il2ks models in definition order. Define models so foreign keys point to earlier ones."""
    return list(apps.get_app_config("il2ks_db").get_models())


def copy_all(source: str, target: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    with transaction.atomic(using=target):
        for model in _app_models():
            rows = list(model._default_manager.using(source).all().order_by("pk"))
            for start in range(0, len(rows), BATCH_SIZE):
                model._default_manager.using(target).bulk_create(rows[start : start + BATCH_SIZE])
            counts[model._meta.db_table] = len(rows)
    connection = connections[target]
    with connection.cursor() as cursor:
        for sql in connection.ops.sequence_reset_sql(no_style(), _app_models()):
            cursor.execute(sql)
    return counts
