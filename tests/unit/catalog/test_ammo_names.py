"""Plain-text ammo names (FR-WEB-18): `ammo.csv` lists every ammo the logs name; `_HIT` / `_xN` follow their parent;
an unknown name is cleaned up, never an error."""

import re
import zipfile
from pathlib import Path

import pytest

import il2ks.core.catalog
from il2ks.core.catalog.loader import Catalog, clean_ammo_name, load_default_catalog, parse_ammo, parse_ordnance

DATA = Path(il2ks.core.catalog.__file__).parent / "data"
LOGS = Path(__file__).parents[2] / "fixtures" / "logs"
NOT_AN_AMMO = {"explosion"}  # the hit lines of every explosion; replay uses them as a link only (TD-08), never shown


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_default_catalog()


def fixture_ammo_names() -> set[str]:
    names: set[str] = set()
    for archive in sorted(LOGS.glob("*.zip")):
        with zipfile.ZipFile(archive) as z:
            for member in z.namelist():
                text = z.read(member).decode("utf-8", "replace")
                names.update(re.findall(r"AMMO:(\S+)", text))
    return names - NOT_AN_AMMO


def test_every_ammo_in_the_fixture_logs_has_an_explicit_entry(catalog: Catalog) -> None:
    names = fixture_ammo_names()
    assert names  # the fixtures do name ammo
    listed = {a.log_name for a in parse_ammo((DATA / "ammo.csv").read_text(encoding="utf-8"))}
    for name in names:
        assert catalog.ammo(name).is_known, name
        # an explicit row of its own, or the row of its `_HIT` / `_xN` parent
        assert re.sub(r"(_HIT|_x\d+)+$", "", name) in listed, name


def test_every_ordnance_hit_ammo_has_a_plain_name(catalog: Catalog) -> None:
    for row in parse_ordnance((DATA / "ordnance.csv").read_text(encoding="utf-8")):
        for ammo in row.hit_ammo:
            assert catalog.ammo(ammo).is_known, ammo


def test_ammo_csv_is_well_formed() -> None:
    rows = parse_ammo((DATA / "ammo.csv").read_text(encoding="utf-8"))
    names = [r.log_name for r in rows]
    assert len(names) == len(set(names))
    for r in rows:
        assert r.name == r.name.strip()
        assert len(r.name) <= 24, r.name
        if r.round_type and r.calibre:  # a gun round: "<calibre> <round type>", a rocket may carry its own name
            assert r.name.endswith(r.round_type) or r.name.startswith(r.calibre), r.log_name


@pytest.mark.parametrize(
    ("log_name", "plain", "designation"),
    [
        ("BULLET_12-7_USA_API", ".50 BMG API", "M8 API"),
        ("BULLET_12-7_USA_INC", ".50 BMG INC", "M1 incendiary"),
        ("BULLET_12-7_USA_APIT", ".50 BMG API-T", "M20 API-T"),
        ("SHELL_23_RUS_HET", "23×115 mm HEI-T", "OZT high-explosive fragmentation tracer"),  # noqa: RUF001 (the data uses a multiplication sign)
        ("RKT_298mm_USA_TinyTim", "Tiny Tim 11.75 in", "Tiny Tim rocket"),
    ],
)
def test_plain_names(catalog: Catalog, log_name: str, plain: str, designation: str) -> None:
    info = catalog.ammo(log_name)
    assert (info.name, info.designation, info.is_known) == (plain, designation, True)


def test_sub_objects_take_the_parent_weapons_name(catalog: Catalog) -> None:
    assert catalog.ammo("RKT_127mm_USA_HVAR_HIT").name == "HVAR 5 in"
    assert catalog.ammo("RKT_127mm_USA_HVAR_SAP_HIT").name == "HVAR 5 in SAP"
    assert catalog.ammo("CLUSTER_9kg_USA_M41_x5").name == "M26 cluster"
    assert catalog.ammo("CLUSTER_9kg_USA_M41_x5_HIT").name == "M26 cluster"
    assert catalog.ammo("CLUSTER_10kg_RUS_PTAB10_x6_HIT").name == "PTAB-10"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("BULLET_12-7_USA_XYZ", "12.7 USA XYZ"),
        ("SHELL_20_USA_HEI_new", "20 USA HEI new"),
        ("SomethingNew", "SomethingNew"),
        ("RKT_HVAR-5", "HVAR-5"),
        ("BULLET_", "BULLET_"),
        ("", ""),
    ],
)
def test_an_unknown_ammo_falls_back_to_a_cleaned_up_name(catalog: Catalog, raw: str, expected: str) -> None:
    info = catalog.ammo(raw)
    assert (info.name, info.is_known, info.designation) == (expected, False, "")
    assert clean_ammo_name(raw) == expected


def test_an_empty_catalog_never_raises() -> None:
    assert Catalog().ammo("BULLET_12-7_USA_API").name == "12.7 USA API"
    assert Catalog().ammo("X_HIT").name == "X HIT"


def test_tiny_tim_is_11_75_inches() -> None:
    ordnance = parse_ordnance((DATA / "ordnance.csv").read_text(encoding="utf-8"))
    assert next(r.info.display_name for r in ordnance if r.info.key == "TINYTIM") == 'Tiny Tim 11.75" rocket'
