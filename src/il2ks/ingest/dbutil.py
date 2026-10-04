"""Small database helpers shared by the ingest modules."""

from collections.abc import Iterable, Sequence

from django.db import models


def update_rows[M: models.Model](model: type[M], rows: Iterable[M], fields: Sequence[str]) -> None:
    """Write `fields` of already-saved, completely loaded `rows` back in as few statements as possible.

    Implemented as `bulk_create(update_conflicts=True)`: `INSERT ... ON CONFLICT (pk) DO UPDATE SET <fields>`, many rows
    per statement. `QuerySet.bulk_update` builds one `CASE WHEN pk = ... THEN ...` per row and field and one
    `filter(pk=...).update(...)` per row compiles a whole query: in both the ORM spends far more time than the database
    does on the writes (about half the ingest time of a mission, then a quarter). Plain value tuples are cheap to
    compile, which makes this several times faster than per-row updates, and it is portable (SQLite 3.24+, Postgres).

    The rows must hold every field (they come from a query without `only`/`defer`, or from `bulk_create`): the INSERT
    half of the statement needs them all, though only `fields` are written when the row exists. Rows that are only
    `Model(pk=..., some_field=...)` go through `update_partial_rows`. Must run inside the caller's transaction."""
    objs = list(rows)
    if objs:
        pk = model._meta.get_field(model._meta.pk.name).attname  # pyright: ignore[reportOptionalMemberAccess]
        model._default_manager.bulk_create(objs, update_conflicts=True, unique_fields=[pk], update_fields=list(fields))


def update_partial_rows[M: models.Model](model: type[M], rows: Iterable[M], fields: Sequence[str]) -> None:
    """Write `fields` of rows that only carry their pk and those fields (`Model(pk=..., field=...)`), one
    `UPDATE ... WHERE pk = ...` per row. For big tables where loading complete rows would be wasteful (a rescoring of
    every sortie); `update_rows` is the fast path for everything else."""
    attnames = [model._meta.get_field(name).attname for name in fields]
    manager = model._default_manager
    for row in rows:
        manager.filter(pk=row.pk).update(**{name: getattr(row, name) for name in attnames})
