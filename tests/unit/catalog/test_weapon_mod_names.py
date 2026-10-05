"""Weapon-mod names in the viewer's language (TD-24): `weapon_mod_names_<language>.csv`, keyed by the English name in
`weapon_mods.csv` (one name is shared by several types, so it is translated once). Pilot-facing equipment terms."""

import csv
import io
from importlib.resources import files

import pytest

from il2ks.core.catalog.loader import Catalog, load_default_catalog, parse_weapon_mod_names, parse_weapon_mods

LANGUAGES = ("de", "es", "fr", "pt-br", "ru")


def data(name: str) -> str:
    return files("il2ks.core.catalog").joinpath("data", name).read_text(encoding="utf-8")


def english_names() -> set[str]:
    return {r["name"] for r in csv.DictReader(io.StringIO(data("weapon_mods.csv")))}


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_default_catalog()


def test_a_mod_name_is_translated_per_language(catalog: Catalog) -> None:
    assert catalog.translated_mod_name("Anti-G suit", "ru") == "Противоперегрузочный костюм"
    assert catalog.translated_mod_name("Anti-G suit", "de") == "Anti-g-Anzug"
    assert catalog.translated_mod_name("Anti-G suit", "pt-br") != "Anti-G suit"
    assert catalog.translated_mod_name("Anti-G suit", "en") == "Anti-G suit"


def test_a_name_nobody_translated_stays_as_it_is(catalog: Catalog) -> None:
    assert catalog.translated_mod_name("Made-up modification", "de") == "Made-up modification"
    assert catalog.translated_mod_name("Anti-G suit", "ko") == "Anti-G suit"


def test_weapon_mods_come_back_in_the_language_and_an_unknown_id_stays_none(catalog: Catalog) -> None:
    wm = 1 | 1 << 1 | 1 << 5 | 1 << 9  # MiG-15bis: NR-23 cannons, Anti-G suit, and id 9 which is not in the catalog
    english = catalog.weapon_mods("MiG-15bis", wm)
    assert [name for _, name in english][:2] == ["NR-23 cannons", "Anti-G suit"]
    russian = catalog.weapon_mods("MiG-15bis", wm, "ru")
    assert [name for _, name in russian][1] == "Противоперегрузочный костюм"
    assert russian[-1] == (9, None)


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_catalog_mod_has_a_translation_in_every_language(language: str) -> None:
    translated = parse_weapon_mod_names(data(f"weapon_mod_names_{language}.csv"))
    assert set(translated) == english_names()


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_english_column_keeps_in_step_and_review_is_valid(language: str) -> None:
    rows = list(csv.DictReader(io.StringIO(data(f"weapon_mod_names_{language}.csv"))))
    assert [r["english"] for r in rows] == sorted(english_names())
    assert {r["review"] for r in rows} <= {"", "llm-draft"}


def test_parse_weapon_mod_names_rejects_bad_rows() -> None:
    header = "english,display_name,review\n"
    with pytest.raises(ValueError, match="review"):
        parse_weapon_mod_names(header + "A,B,maybe\n")
    with pytest.raises(ValueError, match="empty"):
        parse_weapon_mod_names(header + "A,,\n")
    with pytest.raises(ValueError, match="duplicate"):
        parse_weapon_mod_names(header + "A,B,\nA,C,\n")


def test_weapon_mods_csv_still_parses() -> None:
    assert parse_weapon_mods(data("weapon_mods.csv"))
