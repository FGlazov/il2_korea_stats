"""Flavor text (FR-WEB-23): stable variant choice, every variant translatable, the sortie spot rules."""

import hashlib
import re
from pathlib import Path

import pytest
from django.utils import translation

from il2ks.db.models import Player, PlayerSortie, PlayerTour, StatThreshold
from il2ks.devtools import translations
from il2ks.web import flavor
from il2ks.web.flavor import Highlights


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


MIN_VARIANTS = 6  # maintainer, 2026-10-04: every default spot has at least six variants


def test_every_spot_has_several_distinct_variants() -> None:
    for spot, variants in flavor.SPOTS.items():
        texts = [str(v) for v in variants]
        assert len(texts) >= MIN_VARIANTS, spot
        assert len(set(texts)) == len(texts), spot


def test_no_two_variants_of_a_spot_are_identical_in_any_language() -> None:
    """A translation must not collapse two variants into one line (the pick would then repeat it more often)."""
    for code, _directory, _plural in translations.TARGET_LANGUAGES:
        with translation.override(code):
            for spot, variants in flavor.SPOTS.items():
                texts = [str(v) for v in variants]
                assert len(set(texts)) == len(texts), (code, spot)


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


def _first_spot(sortie: PlayerSortie, highlights: Highlights | None = None) -> str | None:
    return next(flavor.sortie_spots(sortie, highlights), None)


def sortie_with(**fields: object) -> PlayerSortie:
    defaults: dict[str, object] = {
        "role": "pilot",
        "outcome": "landed",
        "pilot_status": "healthy",
        "landing_damage": 0.0,
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
        ({"outcome": "shot_down", "strafed_on_ground": True, "takeoffs": 0}, "sortie_strafed"),
        ({"outcome": "shot_down", "strafed_on_ground": True, "takeoffs": 1, "landings": 1}, "sortie_strafed_landed"),
        ({"outcome": "shot_down", "takeoffs": 1, "landings": 1}, None),  # landed but not strafed
        ({"outcome": "shot_down", "loss_class": "aaa"}, "sortie_aa"),
        ({"outcome": "shot_down", "loss_class": "player"}, None),
        ({"kills_air": 3}, "sortie_ace"),
        ({"landing_damage": 0.8}, "sortie_limped_home"),
        ({"landing_damage": 0.2}, None),
        ({"landing_damage": 0.8, "aircraft_status": "unharmed", "damage_taken": 0.0}, "sortie_limped_home"),  # repaired
        ({"landing_damage": 0.8, "outcome": "shot_down"}, None),  # a repaired landing, then shot down: no quip
        ({"role": "gunner", "kills_air": 9}, None),
    ],
)
def test_sortie_spots(fields: dict[str, object], spot: str | None) -> None:
    assert _first_spot(sortie_with(**fields)) == spot


@pytest.mark.parametrize(
    ("fields", "highlights", "spot"),
    [
        ({"outcome": "shot_down", "loss_class": "ai_gunner"}, None, "sortie_ai_gunner"),
        ({"outcome": "landed", "loss_class": "ai_gunner"}, None, None),  # only a loss is "shot down by"
        ({"kills_air": 2}, Highlights(bomber_kills=2), "sortie_bomber_hunter"),
        ({"kills_air": 2}, Highlights(bomber_kills=1), None),  # one bomber is not a hunt
        ({"kills_air": 2}, None, None),  # the spot needs the timeline facts
        ({"assists_air": 2}, None, "sortie_stolen_kills"),  # no air kill: 2 assists
        ({"assists_air": 1}, None, None),
        ({"assists_air": 3, "kills_air": 1}, None, "sortie_stolen_kills"),  # one air kill: 3 assists
        ({"assists_air": 2, "kills_air": 1}, None, None),
        ({"assists_air": 6, "kills_air": 2}, None, "sortie_stolen_kills"),  # two air kills: 6 assists
        ({"assists_air": 5, "kills_air": 2}, None, None),
        # ground assists: 5+ and at least as many as the own ground kills, fewer than 70 of those
        ({"assists_ground": 5}, None, "sortie_stolen_ground"),
        ({"assists_ground": 4}, None, None),
        ({"assists_ground": 9, "kills_ground": 9}, None, "sortie_stolen_ground"),
        ({"assists_ground": 9, "kills_ground": 10}, None, None),
        ({"assists_ground": 80, "kills_ground": 70}, None, "sortie_ground_pounder"),
        ({"assists_ground": 5, "assists_air": 3}, None, "sortie_stolen_kills"),  # the air line comes first
        ({"assists_ground": 5, "kills_air": 3}, None, "sortie_ace"),
        ({"assists_air": 1, "assists_ground": 4}, None, None),  # air and ground assists don't add up
        ({"landing_damage": 0.7, "kills_ground": 2}, None, "sortie_battered_victor"),
        ({"landing_damage": 0.7, "kills_air": 1}, None, "sortie_limped_home"),
        ({"landing_damage": 0.7, "kills_air": 2}, None, "sortie_battered_victor"),
        ({"kills_ground": 70}, None, "sortie_ground_pounder"),
        ({"kills_ground": 69}, None, None),
        ({}, Highlights(first_kill_s=420.0), "sortie_quick_kill"),
        ({}, Highlights(first_kill_s=421.0), None),
        ({}, Highlights(first_kill_s=None), None),
        ({"flight_time_s": 3600.0}, None, "sortie_marathon"),
        ({"flight_time_s": 3599.0}, None, None),
    ],
)
def test_extreme_event_spots(fields: dict[str, object], highlights: Highlights | None, spot: str | None) -> None:
    assert _first_spot(sortie_with(**fields), highlights) == spot


ACHIEVEMENTS: dict[str, object] = {
    "kills_air": 4,
    "assists_air": 8,
    "assists_ground": 90,
    "kills_ground": 90,
    "landing_damage": 0.8,
    "flight_time_s": 4000.0,
}
HUNTER = Highlights(bomber_kills=3, first_kill_s=100.0)
QUICK = Highlights(bomber_kills=0, first_kill_s=100.0)


@pytest.mark.parametrize(
    ("fields", "highlights", "spot"),
    [  # every row has all the conditions of the rows below it too: the first of the documented order wins
        (
            {
                "taxi_accident": True,
                "friendly_kills": 1,
                "is_captured": True,
                "outcome": "shot_down",
                "loss_class": "ai_gunner",
            },
            HUNTER,
            "sortie_taxi",
        ),
        (
            {"friendly_kills": 1, "is_captured": True, "outcome": "shot_down", "loss_class": "ai_gunner"},
            HUNTER,
            "sortie_friendly_fire",
        ),
        ({"is_captured": True, "outcome": "shot_down", "loss_class": "ai_gunner"}, HUNTER, "sortie_captured"),
        ({"outcome": "shot_down", "loss_class": "ai_gunner"}, HUNTER, "sortie_ai_gunner"),
        ({"outcome": "ditched"}, HUNTER, "sortie_ditched"),
        ({"strafed_on_ground": True, "loss_class": "aaa"}, HUNTER, "sortie_strafed"),
        ({"strafed_on_ground": True, "landings": 1, "loss_class": "aaa"}, HUNTER, "sortie_strafed_landed"),
        ({"outcome": "shot_down", "loss_class": "aaa"}, HUNTER, "sortie_aa"),
        ({}, HUNTER, "sortie_bomber_hunter"),
        ({"first_blood": True}, HUNTER, "sortie_bomber_hunter"),  # extreme events come before first blood
        ({"first_blood": True}, QUICK, "sortie_first_blood"),  # ... and first blood before the ace line
        ({}, QUICK, "sortie_ace"),
        ({"kills_air": 0}, QUICK, "sortie_stolen_kills"),
        ({"kills_air": 0, "assists_air": 0, "kills_ground": 5}, QUICK, "sortie_stolen_ground"),
        ({"kills_air": 0, "assists_air": 0, "assists_ground": 0}, QUICK, "sortie_battered_victor"),
        ({"kills_air": 0, "assists_air": 0, "assists_ground": 0, "kills_ground": 0}, QUICK, "sortie_limped_home"),
        (
            {"kills_air": 0, "assists_air": 0, "assists_ground": 0, "landing_damage": 0.0},
            QUICK,
            "sortie_ground_pounder",
        ),
        (
            {"kills_air": 0, "assists_air": 0, "assists_ground": 0, "kills_ground": 0, "landing_damage": 0.0},
            QUICK,
            "sortie_quick_kill",
        ),
        (
            {"kills_air": 0, "assists_air": 0, "assists_ground": 0, "kills_ground": 0, "landing_damage": 0.0},
            None,
            "sortie_marathon",
        ),
    ],
)
def test_spot_precedence(fields: dict[str, object], highlights: Highlights, spot: str) -> None:
    """Accidents and losses first, then the rarest achievements, then the broader ones (`flavor.sortie_spots`)."""
    assert _first_spot(sortie_with(**{**ACHIEVEMENTS, **fields}), highlights) == spot


def test_templates_only_use_known_spots() -> None:
    root = Path(flavor.__file__).parent / "templates"
    used = {m for p in root.rglob("*.html") for m in re.findall(r'\{% flavor "(\w+)"', p.read_text(encoding="utf-8"))}
    assert used
    assert used <= set(flavor.SPOTS)


# --- hall of shame (doc 13): which incidents, and above the p90 ------------------------------------------------------
def _limits(metric: str, p90: float, min_sorties: int = 20) -> StatThreshold:
    return StatThreshold(
        metric=metric, min_sorties=min_sorties, population=100, p10=0, p25=0, p50=0, p75=0, p90=p90, p95=p90, p99=p90
    )


# 5% of sorties end in a taxi accident / a friendly kill at the 90th percentile.
MARKS = {
    "taxi_per_sortie": _limits("taxi_per_sortie", 0.05),
    "friendly_kill_rate": _limits("friendly_kill_rate", 0.05),
}


@pytest.mark.parametrize(
    ("sorties", "taxi", "friendly", "marks", "spot"),
    [
        (50, 0, 0, MARKS, "shame_clean"),
        (50, 1, 0, MARKS, "shame_taxi"),  # 2%: not above the p90
        (50, 0, 1, MARKS, "shame_friendly"),
        (50, 1, 1, MARKS, "shame_both"),
        (50, 3, 0, MARKS, "shame_taxi_p90"),  # 6%
        (50, 0, 3, MARKS, "shame_friendly_p90"),
        (50, 3, 3, MARKS, "shame_both_p90"),
        (50, 3, 1, MARKS, "shame_taxi_p90"),  # only the elevated kind is named
        (50, 1, 3, MARKS, "shame_friendly_p90"),
        (50, 0, 0, {}, "shame_clean"),
        (50, 5, 5, {}, "shame_both"),  # no thresholds (too few pilots): no p90 spot
        (10, 5, 5, MARKS, "shame_both"),  # under the minimum sorties: never "top"
        (50, 0, 0, {"taxi_per_sortie": _limits("taxi_per_sortie", 0.0)}, "shame_clean"),  # zero is not above a p90 of 0
        (50, 3, 0, {"taxi_per_sortie": _limits("taxi_per_sortie", 0.05, min_sorties=60)}, "shame_taxi"),
    ],
)
def test_shame_spot(sorties: int, taxi: int, friendly: int, marks: dict[str, StatThreshold], spot: str) -> None:
    stats = Player(sorties=sorties, taxi_accidents=taxi, friendly_kills=friendly)
    assert flavor.shame_spot(stats, marks) == spot
    assert spot in flavor.SPOTS


def test_shame_spot_on_a_tour_row() -> None:
    """The profile shows a PlayerTour with `?tour=`: the same counters, so the same rules."""
    stats = PlayerTour(sorties=50, taxi_accidents=3, friendly_kills=0)
    assert flavor.shame_spot(stats, MARKS) == "shame_taxi_p90"


# Default quips must fit any pilot, so none may name one aircraft type or a propeller (jets have none).
# Allowed: the place name "MiG Alley" and the Il-10 compliment (it works for any type).
_TYPE_OR_PROPELLER = re.compile(
    r"mustang|sabre|mig-15|f-86|f-51|f-80|f-84|\byak\b|la-11|tu-2|b-29"
    r"|мустанг|сейбр|миг-15|\bяк\b|пропеллер|\bвинт|воздушн\w+ винт"
    r"|propeller|\bprop\b|luftschraube"
    r"|hélice|helice",
    re.IGNORECASE,
)
_ALLOWED = re.compile(r"mig alley|мигов\w+ аллеи", re.IGNORECASE)


def test_no_default_quip_names_an_aircraft_type_or_a_propeller() -> None:
    for code in ("en", *(c for c, _directory, _plural in translations.TARGET_LANGUAGES)):
        with translation.override(code):
            for spot, variants in flavor.SPOTS.items():
                for variant in variants:
                    text = _ALLOWED.sub("", str(variant))
                    assert not _TYPE_OR_PROPELLER.search(text), (code, spot, str(variant))


def _quip_for(sortie: PlayerSortie, quips: dict[str, object]) -> str:
    from django.template import Context

    from il2ks.db.models import SiteSettings
    from il2ks.web.templatetags.il2ks import sortie_flavor

    site = SiteSettings(quips_enabled=True, quips=quips)
    return sortie_flavor(Context({"site": site}), sortie)


def test_a_spot_switched_off_is_skipped_and_the_next_matching_spot_is_used() -> None:
    """A first-blood sortie with three kills matches first blood and ace; with first blood off the ace line shows."""
    sortie = sortie_with(first_blood=True, kills_air=3)
    assert _first_spot(sortie) == "sortie_first_blood"
    line = _quip_for(sortie, {"modes": {"sortie_first_blood": "off"}})
    assert line in {str(v) for v in flavor.SPOTS["sortie_ace"]}


def test_a_spot_with_an_empty_pool_is_skipped_too() -> None:
    """custom_only without any custom quip leaves the pool empty: the next matching spot is used."""
    sortie = sortie_with(first_blood=True, kills_air=3)
    line = _quip_for(sortie, {"modes": {"sortie_first_blood": "custom_only"}})
    assert line in {str(v) for v in flavor.SPOTS["sortie_ace"]}


def test_a_sortie_with_every_matching_spot_off_has_no_quip() -> None:
    sortie = sortie_with(first_blood=True, kills_air=3)
    assert _quip_for(sortie, {"modes": {"sortie_first_blood": "off", "sortie_ace": "off"}}) == ""


def test_every_candidate_spot_is_listed_in_order() -> None:
    sortie = sortie_with(first_blood=True, kills_air=3)
    assert list(flavor.sortie_spots(sortie))[:2] == ["sortie_first_blood", "sortie_ace"]
    assert list(flavor.sortie_spots(sortie_with(role="gunner", kills_air=9))) == []
