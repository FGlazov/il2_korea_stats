"""`il2ks dev dump-db <data dir> <out.jsonl>`: every table's rows, sorted by primary key, one JSON object per line.

For checking that a performance change leaves the database unchanged: dump before and after a full import and diff the
files. Timestamps that depend on when the import ran (`IngestRun` times, `updated_at`) are left out."""

import json
import os
from datetime import datetime
from pathlib import Path

VOLATILE = frozenset({"updated_at"})
VOLATILE_RUN = frozenset({"started_at", "finished_at"})
"""Columns that hold the wall-clock time of the import itself (auto_now fields; `IngestRun` times)."""


def dump_db(data_dir: Path, target: Path) -> int:
    os.environ["IL2KS_DATA_DIR"] = str(data_dir)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "il2ks.settings")
    import django

    django.setup()
    from django.apps import apps

    def encode(value: object) -> str:
        return value.isoformat() if isinstance(value, datetime) else str(value)

    counts: list[str] = []
    with target.open("w", encoding="utf-8", newline="\n") as out:
        for model in sorted(apps.get_app_config("il2ks_db").get_models(), key=lambda m: m._meta.db_table):
            table = model._meta.db_table
            rows = model._default_manager.order_by("pk").values()
            n = 0
            for row in rows.iterator(chunk_size=2000):
                skip = VOLATILE | VOLATILE_RUN if table.endswith("ingestrun") else VOLATILE
                clean = {k: v for k, v in row.items() if k not in skip}
                out.write(f"{table}\t{json.dumps(clean, sort_keys=True, default=encode, ensure_ascii=False)}\n")
                n += 1
            counts.append(f"{table}: {n}")
    print("\n".join(counts))
    return 0
