"""Flavor text (FR-WEB-23): stable variant choice, every variant translatable, the sortie spot rules."""

import hashlib
import re
from pathlib import Path

import pytest
from django.utils import translation

from il2ks.db.models import PlayerSortie
from il2ks.devtools import translations
from il2ks.web import flavor


def test_the_same_seed_always_picks_the_same_variant() -> None:
    for spot in flavor.SPOTS:
        assert [flavor.pick(spot, 42) for _ in range(5)] == [flavor.pick(spot, 42)] * 5


def test_pick_hashes_with_sha256_not_pythons_per_process_hash() -> None:
    """Python's `hash` of a str changes per process: a page would change between restarts and break HTTP caching."""
    variants = flavor.SPOTS["top_pilot"]
    index = int.from_bytes(hashlib.sha256(b"top_pilot:7").digest()[:8], "big") % len(variants)
    assert flavor.pick("top_pilot", 7) is variants[index]


def test_different_seeds_reach_every_variant() -> None:
    for spot, variants in flavor.SPOTS.items():
        chosen = {str(flavor.pick(spot, seed)) for seed in range(300)}
        assert len(chosen) == len(variants), spot


def test_every_spot_has_several_distinct_variants() -> None:
    for spot, variants in flavor.SPOTS.items():
        texts = [str(v) for v in variants]
        assert len(texts) >= 3, spot
        assert len(set(texts)) == len(texts), spot


def test_unknown_spot_fails_loudly() -> None:
    with pytest.raises(KeyError):
        flavor.pick("no_such_spot", 1)


def test_every_variant_is_in_every_translation_catalog() -> None:
    """New variants must be extracted (`il2ks dev translations update`), or they would show in English only."""
    wanted = {str(v) for variants in flavor.SPOTS.values() for v in variants}
    for _code, directory, _plural in translations.TARGET_LANGUAGES:
        have = {m.id for m in translations.read_catalog(directory) if isinstance(m.id, str)}
        assert wanted - have == set(), directory


def test_the_variant_does_not_depend_on_the_language() -> None:
    english = flavor.pick("sortie_taxi", 9)
    with translation.override("ru"):
        assert flavor.pick("sortie_taxi", 9) is english


def sortie_with(**fields: object) -> PlayerSortie:
    defaults: dict[str, object] = {
        "role": "pilot",
        "outcome": "landed",
        "pilot_status": "healthy",
        "aircraft_status": "unharmed",
        "loss_class": "",
    }
    return PlayerSortie(**{**defaults, **fields})


@pytest.mark.parametrize(
    ("fields", "spot"),
    [
        ({}, None),
        ({"kills_air": 2}, None),
        ({"taxi_accident": True, "outcome": "not_taken_off"}, "sortie_taxi"),
        ({"friendly_kills": 1, "kills_air": 5}, "sortie_friendly_fire"),
        ({"is_captured": True, "pilot_fate": "bailed_out"}, "sortie_captured"),
        ({"outcome": "ditched"}, "sortie_ditched"),
        ({"outcome": "shot_down", "loss_class": "aaa"}, "sortie_aa"),
        ({"outcome": "shot_down", "loss_class": "player"}, None),
        ({"kills_air": 3}, "sortie_ace"),
        ({"aircraft_status": "damaged", "damage_taken": 0.8}, "sortie_limped_home"),
        ({"aircraft_status": "damaged", "damage_taken": 0.2}, None),
        ({"role": "gunner", "kills_air": 9}, None),
    ],
)
def test_sortie_spot(fields: dict[str, object], spot: str | None) -> None:
    assert flavor.sortie_spot(sortie_with(**fields)) == spot


def test_templates_only_use_known_spots() -> None:
    root = Path(flavor.__file__).parent / "templates"
    used = {m for p in root.rglob("*.html") for m in re.findall(r'\{% flavor "(\w+)"', p.read_text(encoding="utf-8"))}
    assert used
    assert used <= set(flavor.SPOTS)
