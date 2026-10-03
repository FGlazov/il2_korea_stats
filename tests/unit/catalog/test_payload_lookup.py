"""Payload resolution through the alias list (doc 12 "Payloads")."""

import pytest

from il2ks.core.catalog.loader import Catalog, PayloadInfo, load_default_catalog


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_default_catalog()


def test_alias_resolves_sabre(catalog: Catalog) -> None:
    p = catalog.payload("F-86A-5", 0)
    assert p is not None
    assert p.aircraft == "f-86a"
    assert p.payload_id == 0


def test_direct_name(catalog: Catalog) -> None:
    assert catalog.payload("F-51D", 9) == PayloadInfo("f-51d", 9, "HVAR-6", '6 x HVAR 5" rockets')


def test_case_insensitive(catalog: Catalog) -> None:
    assert catalog.payload("mig-15BIS", 0) == catalog.payload("MiG-15bis", 0) is not None
    assert catalog.payload("f-86a-5", 0) is not None


@pytest.mark.parametrize(
    ("aircraft", "payload_id"),
    [("F-51D", 59), ("Turret_IL10", 0), ("Su-27", 0), ("F-86A-5", -1), ("", 0)],
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
