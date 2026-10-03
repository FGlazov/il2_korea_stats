"""Shipped translations of game object names (TD-24, FR-ADM-5): data files and the language fallback."""

import csv
from pathlib import Path

import pytest

import il2ks.core.catalog
from il2ks.core.catalog.loader import (
    Catalog,
    language_chain,
    load_default_catalog,
    parse_object_names,
)
from il2ks.devtools.translations import TARGET_LANGUAGES

DATA = Path(il2ks.core.catalog.__file__).parent / "data"
LANGUAGES = [code for code, _directory, _plural in TARGET_LANGUAGES]


def read_rows(name: str) -> list[dict[str, str]]:
    with (DATA / name).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_default_catalog()


# --- the data -------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("language", LANGUAGES)
def test_every_translated_language_ships_an_object_names_file(language: str) -> None:
    assert (DATA / f"object_names_{language}.csv").is_file()


@pytest.mark.parametrize("language", LANGUAGES)
def test_names_files_match_the_catalog(language: str) -> None:
    """Each row is an object of `objects.csv`, and its `english` reviewer column is that object's English name."""
    english = {r["log_name"]: r["display_name"] for r in read_rows("objects.csv")}
    rows = read_rows(f"object_names_{language}.csv")
    assert rows
    for row in rows:
        assert row["log_name"] in english, row
        assert row["english"] == english[row["log_name"]], row
        assert row["display_name"].strip(), row
        assert row["review"] in {"", "llm-draft"}, row
        assert row["display_name"] != row["english"], f"{row['log_name']}: same as English, leave the row out"


@pytest.mark.parametrize("language", [c for c in LANGUAGES if c != "ru"])
def test_common_ground_categories_are_translated(catalog: Catalog, language: str) -> None:
    """The generic nouns (Truck, Fence, Barracks, ...) are translated for every category that has some."""
    covered = {
        catalog.lookup(r["log_name"]).ground_category
        for r in read_rows(f"object_names_{language}.csv")
        if catalog.lookup(r["log_name"]).ground_category
    }
    assert {"artillery", "ship", "train", "building", "other", "parked_aircraft"} <= covered


def test_russian_names_the_soviet_aircraft_in_cyrillic(catalog: Catalog) -> None:
    assert catalog.translated_name("MiG-15bis", "ru") == "МиГ-15бис"
    assert catalog.translated_name("Parked MiG-15bis", "ru") == "МиГ-15бис на стоянке"
    assert catalog.translated_name("F-86A-5", "ru") is None  # Western type names stay as they are


# --- the fallback chain ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("language", "chain"),
    [
        ("de", ("de",)),
        ("pt-br", ("pt-br", "pt")),
        ("pt_BR", ("pt-br", "pt")),
        ("PT-BR", ("pt-br", "pt")),
        ("en", ()),
        ("en-gb", ()),
        ("", ()),
    ],
)
def test_language_chain(language: str, chain: tuple[str, ...]) -> None:
    assert language_chain(language) == chain


def test_translation_then_english_then_nothing(catalog: Catalog) -> None:
    assert catalog.translated_name("Fence wire 100m", "de") == "Drahtzaun 100 m"
    assert catalog.translated_name("Fence wire 100m", "ru") == "Проволочное ограждение 100 м"
    assert catalog.translated_name("Fence wire 100m", "en") is None  # English is the default name itself
    assert catalog.translated_name("Fence wire 100m", "ko") is None  # a language nobody translated
    assert catalog.translated_name("F-86A-5", "de") is None  # type names stay: the caller falls back to English
    assert catalog.translated_name("Made-Up-Type", "de") is None  # not in the catalog at all


def test_lookup_ignores_case_and_block_suffixes(catalog: Catalog) -> None:
    assert catalog.translated_name("GAZ_63[64606,0]", "ru") == catalog.translated_name("gaz_63", "ru") == "ГАЗ-63"


def test_regional_language_falls_back_to_its_base_language() -> None:
    names = {"pt": {"Truck": "Caminhão"}, "pt-br": {"Fence": "Cerca"}}
    book = Catalog(object_names=names)

    assert book.translated_name("Truck", "pt-br") == "Caminhão"  # no pt-br row: the base language answers
    assert book.translated_name("Fence", "pt-br") == "Cerca"
    assert book.translated_name("Fence", "pt") is None  # the base language never borrows from a region


def test_the_default_catalog_loads_every_names_file(catalog: Catalog) -> None:
    for language in LANGUAGES:
        assert catalog.translated_name("Fence wire 100m", language), language


# --- parsing --------------------------------------------------------------------------------------------------------
def test_parse_object_names() -> None:
    text = "log_name,english,display_name,review\nFence wire 5m,Fence wire 5m,Drahtzaun 5 m,llm-draft\n"
    assert parse_object_names(text) == {"Fence wire 5m": "Drahtzaun 5 m"}


@pytest.mark.parametrize(
    "text",
    [
        "log_name,display_name\nA,B\n",  # wrong columns
        "log_name,english,display_name,review\nA,A,,\n",  # empty translation
        "log_name,english,display_name,review\n,A,B,\n",  # empty log name
        "log_name,english,display_name,review\nA,A,B,\na,A,C,\n",  # duplicate (case-insensitive)
        "log_name,english,display_name,review\nA,A,B,maybe\n",  # review is empty or llm-draft
    ],
)
def test_parse_object_names_rejects_bad_files(text: str) -> None:
    with pytest.raises(ValueError, match=r"object_names|expected columns"):
        parse_object_names(text)
