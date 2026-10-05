"""Small database helpers shared by the ingest modules."""

from collections.abc import Callable, Iterable, Mapping, Sequence
from operator import itemgetter

from django.db import models
from django.db.models.manager import BaseManager


def _picker(fields: Sequence[str]) -> Callable[[Mapping[str, object]], tuple[object, ...]]:
    """A function that returns the values of `fields` of a mapping as a tuple (`itemgetter`: C speed)."""
    if len(fields) == 1:
        only = fields[0]
        return lambda values: (values[only],)
    return itemgetter(*fields)


IN_BATCH = 900
"""Primary keys per `... WHERE pk IN (...)`. Django does not batch a fast delete and SQLite allows 32,766 variables
(999 before 3.32); a shrinking big tour leaves tens of thousands of stale rows."""


def delete_pks[M: models.Model](objects: BaseManager[M], pks: Iterable[int]) -> None:
    """Delete the rows with these primary keys, `IN_BATCH` per statement. `objects` is a model manager.
    (`bulk_create` is safe already: Django sizes its batches from the backend's variable limit.)"""
    ordered = list(pks)
    for start in range(0, len(ordered), IN_BATCH):
        objects.filter(pk__in=ordered[start : start + IN_BATCH]).delete()


def sync_rows[M: models.Model, K: tuple[object, ...]](
    model: type[M],
    existing_rows: models.QuerySet[M],
    key_fields: Sequence[str],
    value_fields: Sequence[str],
    wanted: Mapping[K, Mapping[str, object]],
    *,
    fixed: Mapping[str, object] | None = None,
) -> None:
    """Make the rows in `existing_rows` equal `wanted` (key tuple -> values): missing rows are inserted, rows whose
    `value_fields` differ are updated, rows no longer wanted are deleted. Existing primary keys are kept.

    `key_fields` are the column names (attnames, `player_id`) of the key, `fixed` is set on inserted rows only. The
    existing rows are read as plain value tuples, not model instances: hydrating tens of thousands of rows to compare a
    few dozen numbers was the main cost of a full-tour refresh. Only the changed rows are built as models, complete
    (key and values), for `update_rows`. Must run inside the caller's transaction."""
    fixed = fixed or {}
    width = len(key_fields)
    existing = {
        tuple(row[1 : 1 + width]): (row[0], row[1 + width :])
        for row in existing_rows.values_list("pk", *key_fields, *value_fields)
    }
    pick = _picker(value_fields)
    changed: list[M] = []
    new: list[M] = []
    for key, values in wanted.items():
        found = existing.pop(key, None)
        if found is None:
            new.append(model(**dict(zip(key_fields, key, strict=True)), **fixed, **values))
        elif found[1] != pick(values):
            changed.append(model(pk=found[0], **dict(zip(key_fields, key, strict=True)), **fixed, **values))
    delete_pks(model._default_manager, [pk for pk, _ in existing.values()])
    update_rows(model, changed, value_fields)
    model._default_manager.bulk_create(new)


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
