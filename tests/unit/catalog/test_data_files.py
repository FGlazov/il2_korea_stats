"""The shipped object and alias data is well-formed (TD-24)."""

import csv
from pathlib import Path

import pytest

import il2ks.core.catalog
from il2ks.core.catalog.loader import (
    GROUND_CATEGORIES,
    GROUND_CLASSES,
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
    assert list(rows[0]) == ["log_name", "display_name", "cls", "is_playable", "propulsion", "ground_category"]
    assert all(r["cls"] in OBJECT_CLASSES for r in rows)
    assert all(r["is_playable"] in ("true", "false") for r in rows)
    assert all(r["log_name"] == r["log_name"].strip() and r["display_name"].strip() for r in rows)


AIRCRAFT_CLASSES = {"fighter", "attacker", "bomber", "transport"}


def test_every_aircraft_has_a_propulsion_and_nothing_else_does() -> None:
    """Rating pools need the propulsion of every aircraft; turrets, vehicles and the rest have none."""
    rows = read_rows("objects.csv")
    aircraft = [r for r in rows if r["cls"] in AIRCRAFT_CLASSES]
    assert aircraft
    assert all(r["propulsion"] in ("prop", "jet") for r in aircraft), [r["log_name"] for r in aircraft]
    assert all(r["propulsion"] == "" for r in rows if r["cls"] not in AIRCRAFT_CLASSES)


def test_every_ground_object_has_a_ground_category_and_nothing_else_does() -> None:
    """OQ-33: ground kills are shown by category, so each object that can be a ground victim needs one."""
    rows = read_rows("objects.csv")
    ground = [r for r in rows if r["cls"] in GROUND_CLASSES]
    assert ground
    assert all(r["ground_category"] in GROUND_CATEGORIES for r in ground), [
        r["log_name"] for r in ground if r["ground_category"] not in GROUND_CATEGORIES
    ]
    assert all(r["ground_category"] == "" for r in rows if r["cls"] not in GROUND_CLASSES)
    assert {r["ground_category"] for r in ground} == set(GROUND_CATEGORIES)  # none of the categories is unused


def test_ground_category_is_independent_of_static() -> None:
    catalog = load_default_catalog()
    truck = catalog.lookup("GAZ_63[64606,0]")  # a static truck
    assert (truck.cls, truck.ground_category, truck.is_static) == ("static", "vehicle", True)
    live_truck = catalog.lookup("GAZ-63")
    assert (live_truck.cls, live_truck.ground_category, live_truck.is_static) == ("vehicle", "vehicle", False)
    assert catalog.lookup("M46 Patton").ground_category == "tank"
    assert catalog.lookup("Fence concrete 200m").ground_category == "other"
    assert catalog.lookup("Military tent A2").ground_category == "building"
    assert catalog.lookup("Parked  Yak-9P").ground_category == "parked_aircraft"
    assert catalog.lookup("Box Car B").ground_category == "train"
    assert catalog.lookup("M2 155mm LongTom").ground_category == "artillery"
    assert catalog.lookup("61-K").ground_category == "aaa"
    assert catalog.lookup("Cargo Ship A").ground_category == "ship"  # a static ship
    assert catalog.lookup("F-51D").ground_category is None
    assert catalog.lookup("Something new").ground_category is None


def test_the_jets_are_the_four_playable_jets() -> None:
    jets = {r["log_name"] for r in read_rows("objects.csv") if r["propulsion"] == "jet"}
    assert jets == {"F-80C-10", "F-84E", "F-86A-5", "MiG-15bis"}
    catalog = load_default_catalog()
    assert catalog.lookup("mig-15BIS").propulsion == "jet"
    assert catalog.lookup("F-51D").propulsion == "prop"
    assert catalog.lookup("B-29").propulsion == "prop"
    assert catalog.lookup("Turret_IL10").propulsion is None
    assert catalog.lookup("Something new").propulsion is None


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
    header = "log_name,display_name,cls,is_playable,propulsion,ground_category\n"
    with pytest.raises(ValueError, match="unknown class"):
        parse_objects(header + "X,X,spaceship,false,,\n")
    with pytest.raises(ValueError, match="is_playable"):
        parse_objects(header + "X,X,tank,yes,,tank\n")
    with pytest.raises(ValueError, match="empty"):
        parse_objects(header + ",X,tank,false,,tank\n")
    with pytest.raises(ValueError, match="propulsion"):
        parse_objects(header + "X,X,fighter,true,rocket,\n")
    with pytest.raises(ValueError, match="unknown ground category"):
        parse_objects(header + "X,X,tank,false,,spaceship\n")
    with pytest.raises(ValueError, match="ground_category"):
        parse_objects(header + "X,X,tank,false,,\n")  # a ground class needs a category
    with pytest.raises(ValueError, match="ground_category"):
        parse_objects(header + "X,X,fighter,true,jet,tank\n")  # and other classes must not have one
    with pytest.raises(ValueError, match="columns"):
        parse_objects("log_name,cls\nX,tank\n")


def test_parse_payloads_and_aliases() -> None:
    payloads = parse_payloads('vehicle,payload_id,editor_name,readable_name\nf-51d,9,HVAR-6,"6 x HVAR 5"" rockets"\n')
    assert payloads[0].readable_name == '6 x HVAR 5" rockets'
    assert parse_payload_aliases("log_name,vehicle\nF-86A-5,f-86a\n") == {"F-86A-5": "f-86a"}
