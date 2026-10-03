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


def test_f51d_payload_ids_are_contiguous_and_match_the_logged_loadouts() -> None:
    """The editor list lacked "M29CLUS-2 + ATAR-6", shifting ids 54-58. AType 10 loads 6 rockets and 2 bombs for id 54,
    4 rockets for 57, 6 rockets and 2 bombs for 58 and 4 rockets and no bombs for 59 (210 samples)."""
    ids = sorted(int(r["payload_id"]) for r in rows() if r["vehicle"] == "f-51d")
    assert ids == list(range(64))
    names = {int(r["payload_id"]): r["editor_name"] for r in rows() if r["vehicle"] == "f-51d"}
    assert names[54] == "M29CLUS-2 + ATAR-6"
    assert names[57] == "NAP_110GAL-2 + ATAR-4"
    assert names[58] == "NAP_110GAL-2 + ATAR-6"
    assert names[59] == "F51_75GAL-2 + ATAR-4"
