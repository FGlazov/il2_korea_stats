"""Object lookups (TD-20, FR-ING-7): case-insensitive, never raise, unknown types flagged."""

import pytest

from il2ks.core.catalog.loader import Catalog, ObjectClass, ObjectInfo, canonical_type_name, load_default_catalog


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_default_catalog()


@pytest.mark.parametrize(
    ("log_name", "cls"),
    [
        ("MiG-15bis", "fighter"),
        ("F-86A-5", "fighter"),
        ("F-80C-10", "fighter"),
        ("F-84E", "fighter"),
        ("F-51D", "fighter"),
        ("Yak-9P", "fighter"),
        ("La-11", "fighter"),
        ("IL-10", "attacker"),
        ("B-29", "bomber"),
        ("Tu-2", "bomber"),
        ("C-47B", "transport"),
        ("Li-2", "transport"),
        ("Turret_IL10", "gunner"),
        ("Turret_B29_1", "gunner"),
        ("T-34-85", "tank"),
        ("M46 Patton", "tank"),
        ("GAZ-63", "vehicle"),
        ("61-K", "aaa"),
        ("Platform Car AA M1919", "aaa"),
        ("Gleaves class destroyer", "ship"),
        ("Landing Ship, Tank", "ship"),
        ("Industrial storage tank D", "static"),
        ("Parked MiG-15bis", "static"),
        ("NAPALM_389kg_USA_110gal", "ordnance"),
        ("FTANK_454L_USA_F86L", "ordnance"),
        ("BotPlanePilot_USSR1950jet", "unknown"),
    ],
)
def test_classification(catalog: Catalog, log_name: str, cls: ObjectClass) -> None:
    info = catalog.lookup(log_name)
    assert info.is_known
    assert info.cls == cls
    assert info.log_name == log_name


def test_player_aircraft_are_playable_and_air(catalog: Catalog) -> None:
    for name in ("MiG-15bis", "F-86A-5", "F-80C-10", "F-84E", "F-51D", "Yak-9P", "La-11", "IL-10", "Turret_IL10"):
        info = catalog.lookup(name)
        assert info.is_playable, name
        assert info.is_air, name


def test_ground_and_ai_objects_are_not_playable(catalog: Catalog) -> None:
    for name in ("B-29", "T-34-85", "Parked MiG-15bis", "BotPlanePilot_USAF1950jet", "Turret_B29_1"):
        assert not catalog.lookup(name).is_playable, name
    assert not catalog.lookup("Parked MiG-15bis").is_air
    assert not catalog.lookup("BotPlanePilot_USAF1950jet").is_air


def test_case_insensitive(catalog: Catalog) -> None:
    info = catalog.lookup("mig-15BIS")
    assert info.is_known
    assert info.log_name == "MiG-15bis"  # original case from the data, not the query
    assert catalog.lookup("  F-86A-5 ") == catalog.lookup("f-86a-5")


def test_comma_names(catalog: Catalog) -> None:
    assert catalog.lookup("Landing Ship, Tank").display_name == "Landing Ship, Tank"
    assert catalog.lookup("Pilot (USAF, jet)").is_known is False  # a display name is not a log name


def test_block_suffix_is_ignored(catalog: Catalog) -> None:
    info = catalog.lookup("GAZ_63[64606,0]")
    assert info.is_known
    assert info.log_name == "GAZ_63"
    assert catalog.lookup("Industrial storage tank D[-1,-1]").cls == "static"


def test_numbered_parachutes(catalog: Catalog) -> None:
    info = catalog.lookup("CParachute_2361344")
    assert info.is_known
    assert info.log_name == "CParachute"


def test_display_names_are_readable(catalog: Catalog) -> None:
    assert catalog.lookup("Turret_IL10").display_name == "IL-10 gunner"
    assert catalog.lookup("GAZ_63").display_name == "GAZ-63"
    assert catalog.lookup("Parked  Yak-9P").display_name == "Parked Yak-9P"
    assert catalog.lookup("MiG-15bis").display_name == "MiG-15bis"


@pytest.mark.parametrize("name", ["Su-27", "", "   ", "[1,2]", "Brand new tank[5,0]", "\x00weird"])
def test_unknown_never_raises(catalog: Catalog, name: str) -> None:
    info = catalog.lookup(name)
    assert not info.is_known
    assert info.cls == "unknown"
    assert not info.is_playable
    assert not info.is_air
    assert info.display_name == info.log_name


def test_unknown_keeps_log_name_without_block_suffix(catalog: Catalog) -> None:
    info = catalog.lookup("Brand new tank[5,0]")
    assert info == ObjectInfo("Brand new tank", "Brand new tank", "unknown", is_playable=False, is_known=False)


def test_empty_catalog() -> None:
    empty = Catalog()
    assert not empty.lookup("MiG-15bis").is_known
    assert empty.payload("MiG-15bis", 0) is None
    assert empty.objects() == ()


def test_duplicate_object_rejected() -> None:
    a = ObjectInfo("MiG-15bis", "MiG-15bis", "fighter", is_playable=True, is_known=True)
    b = ObjectInfo("MIG-15BIS", "x", "fighter", is_playable=True, is_known=True)
    with pytest.raises(ValueError, match="duplicate"):
        Catalog(objects=[a, b])


def test_objects_lists_known_types(catalog: Catalog) -> None:
    objs = catalog.objects()
    assert len(objs) > 400
    assert all(o.is_known for o in objs)
    assert len({o.log_name.casefold() for o in objs}) == len(objs)


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("GAZ_63[64606,0]", "GAZ_63"),
        ("Fence concrete 200m[65913,1]", "Fence concrete 200m"),
        ("Industrial warehouse B9[7841,-1]", "Industrial warehouse B9"),
        ("CParachute_85000", "CParachute"),
        ("Landing Ship, Tank", "Landing Ship, Tank"),
        ("MiG-15bis", "MiG-15bis"),
        ("[1,2]", "[1,2]"),
    ],
)
def test_canonical_type_name(raw: str, canonical: str) -> None:
    assert canonical_type_name(raw) == canonical


@pytest.mark.parametrize(("coalition", "name"), [(1, "REDFOR"), (2, "BLUFOR"), (0, "Neutral"), (3, "Neutral")])
def test_coalition_name(catalog: Catalog, coalition: int, name: str) -> None:
    assert catalog.coalition_name(coalition) == name
