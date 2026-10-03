"""The shipped object and alias data is well-formed (TD-24)."""

import csv
from pathlib import Path

import pytest

import il2ks.core.catalog
from il2ks.core.catalog.loader import (
    OBJECT_CLASSES,
    load_default_catalog,
    parse_objects,
    parse_payload_aliases,
    parse_payloads,
)

DATA = Path(il2ks.core.catalog.__file__).parent / "data"


def read_rows(name: str) -> list[dict[str, str]]:
    with (DATA / name).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_objects_columns_and_values() -> None:
    rows = read_rows("objects.csv")
    assert list(rows[0]) == ["log_name", "display_name", "cls", "is_playable"]
    assert all(r["cls"] in OBJECT_CLASSES for r in rows)
    assert all(r["is_playable"] in ("true", "false") for r in rows)
    assert all(r["log_name"] == r["log_name"].strip() and r["display_name"].strip() for r in rows)


def test_object_names_unique_case_insensitively() -> None:
    names = [r["log_name"].casefold() for r in read_rows("objects.csv")]
    assert len(names) == len(set(names))


def test_aliases_point_at_payload_vehicles() -> None:
    vehicles = {r["vehicle"] for r in read_rows("payloads.csv")}
    objects = {r["log_name"] for r in read_rows("objects.csv")}
    for row in read_rows("payload_aliases.csv"):
        assert row["vehicle"] in vehicles, row
        assert row["log_name"] in objects, row


def test_loads() -> None:
    catalog = load_default_catalog()
    assert len(catalog.objects()) == len(read_rows("objects.csv"))


def test_parse_objects_rejects_bad_rows() -> None:
    header = "log_name,display_name,cls,is_playable\n"
    with pytest.raises(ValueError, match="unknown class"):
        parse_objects(header + "X,X,spaceship,false\n")
    with pytest.raises(ValueError, match="is_playable"):
        parse_objects(header + "X,X,tank,yes\n")
    with pytest.raises(ValueError, match="empty"):
        parse_objects(header + ",X,tank,false\n")
    with pytest.raises(ValueError, match="columns"):
        parse_objects("log_name,cls\nX,tank\n")


def test_parse_payloads_and_aliases() -> None:
    payloads = parse_payloads('vehicle,payload_id,editor_name,readable_name\nf-51d,9,HVAR-6,"6 x HVAR 5"" rockets"\n')
    assert payloads[0].readable_name == '6 x HVAR 5" rockets'
    assert parse_payload_aliases("log_name,vehicle\nF-86A-5,f-86a\n") == {"F-86A-5": "f-86a"}
