"""Weapon modifications: the `WM` bitmask decode (doc 12) and the shipped `weapon_mods.csv`."""

import pytest

from il2ks.core.catalog.loader import (
    Catalog,
    WeaponModInfo,
    load_default_catalog,
    parse_weapon_mods,
    weapon_mod_ids,
    weapon_mod_mask,
)


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_default_catalog()


def test_base_bit_alone_means_no_modification() -> None:
    assert weapon_mod_ids(1) == ()
    assert weapon_mod_ids(0) == ()  # a turret's WM
    assert weapon_mod_ids(-1) == ()


@pytest.mark.parametrize("mod_id", range(1, 8))
def test_each_bit_is_its_own_mod(mod_id: int) -> None:
    assert weapon_mod_ids(1 | 1 << mod_id) == (mod_id,)
    assert weapon_mod_ids(1 << mod_id) == (mod_id,)  # the base bit does not matter


def test_several_bits_are_listed_ascending() -> None:
    assert weapon_mod_ids(19) == (1, 4)  # 0b10011
    assert weapon_mod_ids(63) == (1, 2, 3, 4, 5)


def test_mask_is_the_inverse() -> None:
    assert weapon_mod_mask([1, 4]) == 0b10010
    assert weapon_mod_ids(1 | weapon_mod_mask([2, 5])) == (2, 5)
    assert weapon_mod_mask([]) == 0


def test_names_resolve_through_the_alias(catalog: Catalog) -> None:
    assert catalog.weapon_mods("MiG-15bis", 19) == ((1, "NR-23 cannons"), (4, "Additional armor protection"))
    assert catalog.weapon_mods("F-86A-5", 3) == ((1, "A-1CM Gunsight"),)
    assert catalog.weapon_mods("F-51D", 1) == ()


def test_unknown_ids_and_types_keep_the_raw_id(catalog: Catalog) -> None:
    assert catalog.weapon_mods("MiG-15bis", 1 | 1 << 9) == ((9, None),)
    assert catalog.weapon_mods("Su-27", 3) == ((1, None),)


def test_parse_and_duplicates() -> None:
    mods = parse_weapon_mods("vehicle,mod_id,name\nyak-9p,1,Artificial horizon\n")
    assert mods == [WeaponModInfo("yak-9p", 1, "Artificial horizon")]
    with pytest.raises(ValueError, match="duplicate"):
        Catalog(weapon_mods=[*mods, *mods])
    with pytest.raises(ValueError, match="mod_id"):
        parse_weapon_mods("vehicle,mod_id,name\nyak-9p,0,Base\n")
