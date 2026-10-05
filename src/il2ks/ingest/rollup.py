"""All-time rows as a roll-up of per-tour rows (doc 14 "Level 2 is per tour"; maintainer requirement 2026-10-05: "all
time stats can be built on top of the sum of all tour stats").

A tour refresh reads only that tour's level-1 rows and writes the tour's rows. The all-time row of the same entity is
then a SUM / MAX / MIN over its tour rows: `rollup` reads the tour rows of the affected entities (never level 1) and
writes the all-time rows, only those whose values changed.

Determinism (a rebuild must equal an incremental update to the last digit): the tour rows of a key are folded in tour
order (`order_by`, default the tour id), and a float total is rounded at write (`ROUND_DECIMALS`), so a SUM that a
database would have added in another order cannot differ in the last bits.

Used by `ingest.aggregates` (player counters, aircraft, pools), `ingest.builds`, `ingest.pairs`, `ingest.type_board`
and the identity roll-up; the aircraft side may use it the same way.
"""

from collections.abc import Callable, Mapping, Sequence
from typing import cast

from django.db import models
from django.db.models.manager import BaseManager

from il2ks.ingest.dbutil import IN_BATCH, sync_rows, update_rows

ROUND_DECIMALS = 4
"""Float totals (flight time, friendly damage, time on target, scores) are stored rounded to this many decimals."""

type Row = Mapping[str, object]
type Derive = Callable[[Sequence[Row]], object]
"""A value computed from all the tour rows of one key (in tour order), for what is no plain SUM / MAX / MIN (the
newest row's mission, the most used aircraft)."""


def rollup[M: models.Model, S: models.Model](
    model: type[M],
    existing: models.QuerySet[M] | BaseManager[M],
    source: models.QuerySet[S] | BaseManager[S],
    *,
    key: Sequence[str],
    sums: Sequence[str] = (),
    maxes: Sequence[str] = (),
    mins: Sequence[str] = (),
    derived: Mapping[str, Derive] | None = None,
    source_fields: Sequence[str] = (),
    fixed: Mapping[str, object] | None = None,
    model_key: Sequence[str] | None = None,
    order_by: str = "tour_id",
    update_only: bool = False,
) -> None:
    """Make the all-time rows in `existing` equal the roll-up of `source` (the per-tour rows of the same entities).

    - `key`: the fields that identify an entity in `source` (`player_id`, `aircraft_id`, ...); `model_key` names them on
      `model` when they differ (`player_id` -> `pk` for `Player`). `fixed` is set on rows that are created (`tour=None`
      for a table that holds both scopes).
    - `sums` / `maxes` / `mins`: the fields rolled up (a field is written under the same name).
    - `derived`: field name -> function of the key's rows in tour order; `source_fields` are the extra source columns
      those functions read.
    - Default: rows are created, updated and deleted (a key without tour rows has no all-time row). `update_only`: the
      rows in `existing` are only updated, and one without tour rows gets zero sums and keeps its maxes, mins and
      derived values (the `Player` table, whose rows are identities that never go away).
    Writes only the rows whose values changed. Must run inside the caller's transaction."""
    derived = derived or {}
    fixed = fixed or {}
    model_key = tuple(model_key or key)
    fields = [*sums, *maxes, *mins, *derived]
    columns = [*key, *sums, *maxes, *mins, *source_fields]
    grouped: dict[tuple[object, ...], list[Row]] = {}
    for row in source.order_by(order_by).values(*columns):
        grouped.setdefault(tuple(row[k] for k in key), []).append(row)
    wanted = {k: _fold(rows, sums, maxes, mins, derived) for k, rows in grouped.items()}

    if update_only:
        _update_existing(model, existing, model_key, fields, sums, wanted)
    else:
        sync_rows(model, existing, model_key, fields, wanted, fixed=fixed)


def _update_existing[M: models.Model](
    model: type[M],
    existing: models.QuerySet[M] | BaseManager[M],
    model_key: Sequence[str],
    fields: Sequence[str],
    sums: Sequence[str],
    wanted: Mapping[tuple[object, ...], Mapping[str, object]],
) -> None:
    """`update_only`: the rows of `existing` are never created or deleted; one without tour rows gets zero sums.
    The rows are compared as value tuples and only the changed ones are loaded (complete, for `update_rows`)."""
    zero = dict.fromkeys(sums, 0)
    current = {
        tuple(row[1 : 1 + len(model_key)]): (row[0], row[1 + len(model_key) :])
        for row in existing.values_list("pk", *model_key, *fields)
    }
    new_values: dict[object, Mapping[str, object]] = {}
    for key, (pk, values) in current.items():
        target = wanted.get(key) or zero
        if any(target.get(name, value) != value for name, value in zip(fields, values, strict=True)):
            new_values[pk] = target
    changed: list[M] = []
    pks = list(new_values)
    for start in range(0, len(pks), IN_BATCH):  # a 2,000-player chunk can change more rows than SQLite has variables
        for row in model._default_manager.filter(pk__in=pks[start : start + IN_BATCH]):
            for name, value in new_values[row.pk].items():
                setattr(row, name, value)
            changed.append(row)
    update_rows(model, changed, fields)


def _fold(
    rows: Sequence[Row], sums: Sequence[str], maxes: Sequence[str], mins: Sequence[str], derived: Mapping[str, Derive]
) -> dict[str, object]:
    values: dict[str, object] = {}
    totals: dict[str, float] = dict.fromkeys(sums, 0)
    for row in rows:  # in tour order: the same additions whatever order the database returns
        for name in sums:
            totals[name] += cast(float, row[name])
    for name, total in totals.items():
        values[name] = round(total, ROUND_DECIMALS) if isinstance(total, float) else total
    for name in maxes:
        present = [row[name] for row in rows if row[name] is not None]
        values[name] = max(present) if present else None  # pyright: ignore[reportArgumentType]
    for name in mins:
        present = [row[name] for row in rows if row[name] is not None]
        values[name] = min(present) if present else None  # pyright: ignore[reportArgumentType]
    for name, derive in derived.items():
        values[name] = derive(rows)
    return values
