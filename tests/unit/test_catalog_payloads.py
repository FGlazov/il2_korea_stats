"""The shipped payload catalog (TD-24, doc 12 'Payloads') is well-formed."""

import csv
from pathlib import Path

import il2ks.core.catalog

PAYLOADS = Path(il2ks.core.catalog.__file__).parent / "data" / "payloads.csv"
PLAYER_AIRCRAFT = {"f-51d", "f-80c-10", "f-84e", "f-86a", "il-10", "la-11", "mig-15bis", "yak-9p"}


def rows() -> list[dict[str, str]]:
    with PAYLOADS.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_columns() -> None:
    with PAYLOADS.open(encoding="utf-8", newline="") as f:
        header = next(csv.reader(f))
    assert header == ["vehicle", "payload_id", "editor_name", "readable_name"]


def test_keys_unique_and_ids_numeric() -> None:
    keys = [(r["vehicle"], int(r["payload_id"])) for r in rows()]
    assert len(keys) == len(set(keys))


def test_every_row_has_a_readable_name() -> None:
    assert all(r["readable_name"].strip() for r in rows())


def test_covers_all_player_aircraft() -> None:
    assert {r["vehicle"] for r in rows()} >= PLAYER_AIRCRAFT
