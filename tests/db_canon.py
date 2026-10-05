"""A primary-key-free dump of the database, to compare two ways of reaching the same data (FR-ING-15 tests).

Rows created in a different order get different primary keys, so every row is described by its own fields with each
foreign key replaced by the (recursively described) row it points at. Sorties are identified by their natural key
`(mission, account UUID, spawn tick)`, and the sortie ids inside the damage/timeline JSON are replaced the same way.
"""

import json
from datetime import datetime
from typing import Any, cast

from django.apps import apps
from django.db import models

SKIP_MODELS = frozenset(
    {
        "IngestRun",
        "LiveMission",
        "LivePlayer",
        "DataVersion",
        "SiteSettings",
        "NavLink",
        "ReprocessRequest",
    }
)
VOLATILE = frozenset({"updated_at"})
OBJECT_ID_KEYED = frozenset({"kills_with_counts", "deaths_in_counts"})
"""JSON fields keyed by a GameObject primary key (a player's own aircraft type id -> count). The ids differ between two
ways to the same data (Postgres sequences are not rolled back, SQLite reuses ids), so the keys are replaced by the
described row, as the sortie ids are."""
FLOAT_DIGITS = 6
"""Float columns are compared rounded: a SUM over the same rows in another order (Postgres does not promise one) differs
in the last bits (256.53999999999996 vs 256.54), which is no difference in what the test checks."""


def _encode(value: object) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)


class Canon:
    def __init__(self) -> None:
        self._memo: dict[tuple[str, int], str] = {}
        self._rows: dict[str, dict[int, dict[str, Any]]] = {}
        self._sortie_key: dict[int, str] = {}

    def _all(self, model: type[models.Model]) -> dict[int, dict[str, Any]]:
        label = model.__name__
        if label not in self._rows:
            self._rows[label] = {obj["id"]: obj for obj in model._default_manager.values()}
        return self._rows[label]

    def key(self, model: type[models.Model], pk: int | None) -> str:
        if pk is None:
            return "None"
        memo_key = (model.__name__, pk)
        found = self._memo.get(memo_key)
        if found is not None:
            return found
        row = self._all(model)[pk]
        if model.__name__ == "PlayerSortie":
            mission = self._mission_uid(row["mission_id"])
            text = f"sortie:{mission}:{row['account_uuid']}:{row['spawn_tick']}"
        else:
            text = json.dumps(self.fields(model, row), sort_keys=True, default=_encode, ensure_ascii=False)
        self._memo[memo_key] = text
        return text

    def _mission_uid(self, pk: int) -> str:
        from il2ks.db.models import Mission

        return str(self._all(Mission)[pk]["mission_uid"])

    def fields(self, model: type[models.Model], row: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for field in model._meta.concrete_fields:
            if field.primary_key or field.name in VOLATILE:
                continue
            if isinstance(field, models.ForeignKey):
                target = cast(type[models.Model], field.related_model)  # pyright: ignore[reportUnknownMemberType]
                out[field.name] = self.key(target, row[field.attname])
            elif field.name in OBJECT_ID_KEYED:
                out[field.name] = self._remap_object_keys(row[field.attname])
            else:
                out[field.name] = self._remap_json(row[field.attname])
        return out

    def _remap_object_keys(self, counts: dict[str, int]) -> dict[str, int]:
        from il2ks.db.models import GameObject

        return {self.key(GameObject, int(pk)): n for pk, n in counts.items()}

    def _remap_json(self, value: object) -> object:
        from il2ks.db.models import PlayerSortie

        if isinstance(value, dict):
            return {
                k: (self.key(PlayerSortie, v) if k == "sortie_id" and isinstance(v, int) else self._remap_json(v))
                for k, v in cast(dict[str, Any], value).items()
            }
        if isinstance(value, list):
            return [self._remap_json(v) for v in cast(list[Any], value)]
        if isinstance(value, float):
            return round(value, FLOAT_DIGITS) + 0.0  # + 0.0: -0.0 becomes 0.0
        return value


def canonical_dump() -> dict[str, list[str]]:
    """Every table (but the volatile ones) as a sorted list of pk-free JSON rows."""
    canon = Canon()
    dump: dict[str, list[str]] = {}
    for model in apps.get_app_config("il2ks_db").get_models():
        if model.__name__ in SKIP_MODELS:
            continue
        rows = [
            json.dumps(canon.fields(model, row), sort_keys=True, default=_encode, ensure_ascii=False)
            for row in canon._all(model).values()  # pyright: ignore[reportPrivateUsage]
        ]
        dump[model.__name__] = sorted(rows)
    return dump


def diff_dumps(a: dict[str, list[str]], b: dict[str, list[str]]) -> list[str]:
    """Human-readable differences: which tables differ and one example row each way."""
    problems: list[str] = []
    for table in sorted(set(a) | set(b)):
        left, right = a.get(table, []), b.get(table, [])
        if left == right:
            continue
        only_left = [r for r in left if r not in right]
        only_right = [r for r in right if r not in left]
        problems.append(
            f"{table}: {len(left)} vs {len(right)} rows; only in first: {only_left[:1]}; "
            f"only in second: {only_right[:1]}"
        )
    return problems
