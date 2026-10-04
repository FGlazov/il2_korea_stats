"""Small database helpers shared by the ingest modules."""

from collections.abc import Iterable, Sequence

from django.db import models


def update_rows[M: models.Model](model: type[M], rows: Iterable[M], fields: Sequence[str]) -> None:
    """Write `fields` of already-saved `rows` back, one `UPDATE ... WHERE pk = ...` per row.

    Used instead of `QuerySet.bulk_update`: that builds one `CASE WHEN pk = ... THEN ...` per row and field, and the ORM
    spends far more time resolving those expressions than the database spends on the writes (about half of the ingest
    time of a mission before this helper). Plain per-row updates are simple statements the ORM compiles quickly; with
    SQLite there is no round trip to pay for. Same result as `bulk_update`; must run inside the caller's transaction."""
    attnames = [model._meta.get_field(name).attname for name in fields]
    manager = model._default_manager
    for row in rows:
        manager.filter(pk=row.pk).update(**{name: getattr(row, name) for name in attnames})
