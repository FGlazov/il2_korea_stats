"""Payload resolution through the alias list (doc 12 "Payloads")."""

import pytest

from il2ks.core.catalog.loader import Catalog, PayloadInfo, load_default_catalog


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_default_catalog()


def test_alias_resolves_sabre(catalog: Catalog) -> None:
    p = catalog.payload("F-86A-5", 0)
    assert p is not None
    assert p.aircraft == "f-86a-5"
    assert p.payload_id == 0


def test_direct_name(catalog: Catalog) -> None:
    assert catalog.payload("F-51D", 9) == PayloadInfo("f-51d", 9, "HVAR-6", '6 x HVAR 5" rockets')


def test_case_insensitive(catalog: Catalog) -> None:
    assert catalog.payload("mig-15BIS", 0) == catalog.payload("MiG-15bis", 0) is not None
    assert catalog.payload("f-86a-5", 0) is not None


@pytest.mark.parametrize(
    ("aircraft", "payload_id"),
    [("F-51D", 999), ("Turret_IL10", 0), ("Su-27", 0), ("F-86A-5", -1), ("", 0)],
)
def test_unresolved_returns_none(catalog: Catalog, aircraft: str, payload_id: int) -> None:
    assert catalog.payload(aircraft, payload_id) is None


def test_every_player_aircraft_has_payload_zero(catalog: Catalog) -> None:
    for obj in catalog.objects():
        if obj.is_playable and obj.cls != "gunner":
            assert catalog.payload(obj.log_name, 0) is not None, obj.log_name


def test_alias_without_csv_fallback() -> None:
    p = PayloadInfo("f-86a", 1, "X", "Y")
    c = Catalog(payloads=[p], payload_aliases={"F-86A-5": "f-86a"})
    assert c.payload("F-86A-5", 1) == p
    assert c.payload("f-86a", 1) == p  # the vehicle key itself also matches
    assert Catalog(payloads=[p]).payload("F-86A-5", 1) is None  # no alias, no match


def test_f86_alias_is_the_new_vehicle_key(catalog: Catalog) -> None:
    """The payload table renamed the Sabre to `f-86a-5`; the shipped alias must follow."""
    assert catalog.payload("F-86A-5", 1) is not None
    assert catalog.payload("F-86A-5", 1) == catalog.payload("f-86a-5", 1)


def test_il10_uses_the_renumbered_ids(catalog: Catalog) -> None:
    """One row per (vehicle, id), the latest table: il-10 id 25 is PTAB + 2 x FAB-100 (it was 128 x AO-2.5)."""
    p = catalog.payload("IL-10", 25)
    assert p is not None
    assert p.editor_name == "PTAB1025-60 + FAB100sc-2"
