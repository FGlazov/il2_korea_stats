"""Significant weapon modifications (`weapon_mods.csv`, `significant`) and the filter patterns of a sortie."""

import pytest

from il2ks.core.catalog.loader import Catalog, load_default_catalog, mod_filter_pattern, mod_filter_patterns


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_default_catalog()


def test_significant_mods_are_the_maintainers_list(catalog: Catalog) -> None:
    mig = catalog.significant_mods("MiG-15bis")
    assert [(m.mod_id, m.name) for m in mig] == [
        (1, "NR-23 cannons"),
        (2, "Improved air brakes and wing"),
        (5, "Anti-G suit"),
    ]
    assert [(m.mod_id, m.name) for m in catalog.significant_mods("F-51D")] == [(4, "150-grade fuel")]
    assert catalog.significant_mods("F-86A-5") == ()
    assert catalog.significant_mods("Su-27") == ()


def test_filter_patterns_of_a_sortie() -> None:
    # MiG significant mods 1, 2, 5; a sortie with mods 1 and 5 (WM = base | 1 << 1 | 1 << 5)
    patterns = mod_filter_patterns(1 | 1 << 1 | 1 << 5, (1, 2, 5))
    # every combination of "any" and the sortie's own state (+ - +), but not all "any"
    assert set(patterns) == {"+**", "*-*", "**+", "+-*", "+*+", "*-+", "+-+"}
    assert len(patterns) == 7
    assert list(patterns) == sorted(patterns)


def test_a_lone_mod_and_a_type_without() -> None:
    assert mod_filter_patterns(1, (4,)) == ("-",)
    assert mod_filter_patterns(1 | 1 << 4, (4,)) == ("+",)
    assert mod_filter_patterns(35, ()) == ()


def test_pattern_of_states() -> None:
    assert mod_filter_pattern(["*", "*", "*"]) == ""
    assert mod_filter_pattern(["+", "*", "-"]) == "+*-"
    assert mod_filter_pattern([]) == ""
