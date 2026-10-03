"""Ordnance reference data (FR-WEB-18): payload tokens, named hit-line ammo, loadouts."""

import csv
from pathlib import Path

import pytest

import il2ks.core.catalog
from il2ks.core.catalog.loader import (
    Catalog,
    OrdnanceRow,
    load_default_catalog,
    parse_ordnance,
    parse_payloads,
)

DATA = Path(il2ks.core.catalog.__file__).parent / "data"


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_default_catalog()


def test_every_payload_of_every_aircraft_resolves_to_a_loadout(catalog: Catalog) -> None:
    """A payload token without an ordnance row would make the loadout unknown (None)."""
    aliases = {v: k for k, v in csv.reader((DATA / "payload_aliases.csv").open(encoding="utf-8"))}
    for p in parse_payloads((DATA / "payloads.csv").read_text(encoding="utf-8")):
        log_name = aliases.get(p.aircraft, p.aircraft)
        assert catalog.loadout(log_name, p.payload_id) is not None, (p.aircraft, p.payload_id, p.editor_name)


def test_loadout_lists_each_type_with_its_count(catalog: Catalog) -> None:
    items = catalog.loadout("F-51D", 11)  # "M64-2 + HVAR-4" in the payload file
    assert items is not None
    assert [(i.ordnance.key, i.ordnance.kind, i.count) for i in items] == [("M64", "bomb", 2), ("HVAR", "rocket", 4)]


def test_loadout_knows_tanks_pylons_and_empty(catalog: Catalog) -> None:
    kinds = {
        tuple(i.ordnance.kind for i in items)
        for payload_id in range(0, 12)
        if (items := catalog.loadout("F-80C-10", payload_id)) is not None
    }
    assert ("inert",) in kinds
    assert ("tank",) in kinds
    assert ("tank", "bomb") in kinds


def test_an_unknown_payload_is_unknown_not_empty(catalog: Catalog) -> None:
    assert catalog.loadout("F-51D", 59_000) is None
    assert catalog.loadout("Never heard of it", 1) is None
    assert Catalog().loadout("F-51D", 1) is None


@pytest.mark.parametrize(
    ("ammo", "key"),
    [
        ("BOMB_449kg_USA_M65", "M65"),
        ("BOMB_123kg_RUS_FAB100sc", "FAB100"),
        ("RKT_127mm_USA_HVAR", "HVAR"),
        ("RKT_127mm_USA_HVAR_HIT", "HVAR"),
        ("RKT_127mm_USA_HVAR_SAP", "HVAR_SAP"),  # the longest name wins over HVAR
        ("RKT_127mm_USA_HVAR_SAP_HIT", "HVAR_SAP"),
        ("RKT_298mm_USA_TinyTim_HIT", "TINYTIM"),
        ("RKT_132mm_RUS_M13UK", "M13"),
        ("NapalmBullet", "NAPALM"),
        ("CLUSTER_1kg_RUS_PTAB2_HIT", "PTAB2"),
        ("CLUSTER_1kg_RUS_PTAB2_x8", "PTAB2"),
        ("CLUSTER_10kg_RUS_PTAB10_x6", "PTAB10"),
        ("CLUSTER_10kg_RUS_AO10sc_HIT", "AO10"),
        ("CLUSTER_2kg_RUS_AO2sc_x8", "AO2"),
        ("CLUSTER_9kg_USA_M41_HIT", "M26"),
        ("CLUSTER_2kg_USA_M83_x10", "M29"),
    ],
)
def test_named_hit_ammo_maps_to_its_ordnance(catalog: Catalog, ammo: str, key: str) -> None:
    info = catalog.ordnance_for_ammo(ammo)
    assert info is not None
    assert info.key == key


@pytest.mark.parametrize(
    "ammo", ["BULLET_12-7_USA_API", "SHELL_23_RUS_HET", "NPC_SHELL_ENG_40_HE", "FlareWhite", "explosion", ""]
)
def test_guns_and_flares_are_not_ordnance(catalog: Catalog, ammo: str) -> None:
    assert catalog.ordnance_for_ammo(ammo) is None


def test_ordnance_csv_is_well_formed() -> None:
    rows = parse_ordnance((DATA / "ordnance.csv").read_text(encoding="utf-8"))
    keys = [r.info.key for r in rows]
    assert len(keys) == len(set(keys))
    tokens = [t.casefold() for r in rows for t in r.payload_tokens]
    assert len(tokens) == len(set(tokens))
    ammo = [a for r in rows for a in r.hit_ammo]
    assert len(ammo) == len(set(ammo))
    assert all(r.payload_tokens for r in rows)
    assert {r.info.kind for r in rows} == {"bomb", "rocket", "napalm", "flare", "tank", "inert"}


def test_ordnance_csv_rejects_a_bad_kind_and_a_shared_token() -> None:
    with pytest.raises(ValueError, match="unknown kind"):
        parse_ordnance("key,kind,display_name,payload_tokens,hit_ammo\nX,bazooka,X,X,\n")
    rows: list[OrdnanceRow] = parse_ordnance(
        "key,kind,display_name,payload_tokens,hit_ammo\nA,bomb,A,T,\nB,bomb,B,T,\n"
    )
    with pytest.raises(ValueError, match="two ordnance types"):
        Catalog(ordnance=rows)
