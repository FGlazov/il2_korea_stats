"""Air and ground score of one sortie (FR-WEB-7, FR-ADM-7)."""

from il2ks.core.catalog.loader import GroundCategory
from il2ks.core.ratings.score import DEFAULT_SCORE_RULES, ScoreRules, SortieFacts, SortieScore, score_sortie

RULES = ScoreRules()


def facts(
    *,
    attack: bool = False,
    kills_air_pvp: int = 0,
    kills_air_ai: int = 0,
    assists: int = 0,
    kills_ground: dict[GroundCategory, int] | None = None,
    is_death: bool = False,
    is_plane_lost: bool = False,
    is_captured: bool = False,
    suspected_early_bailout: bool = False,
    friendly_kills: int = 0,
) -> SortieFacts:
    return SortieFacts(
        attack=attack,
        kills_air_pvp=kills_air_pvp,
        kills_air_ai=kills_air_ai,
        assists=assists,
        kills_ground=kills_ground or {},
        is_death=is_death,
        is_plane_lost=is_plane_lost,
        is_captured=is_captured,
        suspected_early_bailout=suspected_early_bailout,
        friendly_kills=friendly_kills,
    )


def test_an_empty_sortie_scores_nothing() -> None:
    assert score_sortie(facts()) == SortieScore(0.0, 0.0)


def test_air_kills_and_assists_make_the_air_score() -> None:
    result = score_sortie(facts(kills_air_pvp=2, kills_air_ai=3, assists=1))
    assert result == SortieScore(air=2 * 10 + 3 * 2 + 3, ground=0.0)


def test_ground_kills_are_valued_by_category_and_fences_are_trivial() -> None:
    result = score_sortie(facts(kills_ground={"tank": 1, "building": 2, "other": 10}))
    assert result.ground == 6 + 2 * 2 + 10 * 0.2
    assert result.air == 0.0
    assert RULES.ground_other < RULES.ground_vehicle < RULES.ground_tank


def test_penalties_are_charged_to_the_air_score_of_an_air_superiority_sortie() -> None:
    result = score_sortie(
        facts(is_death=True, is_plane_lost=True, is_captured=True, suspected_early_bailout=True, friendly_kills=1)
    )
    assert result == SortieScore(air=-(3 + 2 + 2 + 5 + 3), ground=0.0)


def test_penalties_are_charged_to_the_ground_score_of_an_attack_sortie() -> None:
    result = score_sortie(facts(attack=True, is_death=True, is_plane_lost=True, kills_air_pvp=1))
    assert result == SortieScore(air=10.0, ground=-5.0)


def test_ground_kills_in_a_fighter_sortie_still_count_for_the_ground_score() -> None:
    result = score_sortie(facts(kills_ground={"vehicle": 2}, is_death=True))
    assert result == SortieScore(air=-3.0, ground=6.0)


def test_rules_are_applied_and_zero_switches_a_value_off() -> None:
    rules = ScoreRules(air_kill_pvp=1.0, penalty_death=0.0)
    assert score_sortie(facts(kills_air_pvp=4, is_death=True), rules) == SortieScore(4.0, 0.0)
    assert ScoreRules() == DEFAULT_SCORE_RULES


def test_friendly_kills_are_penalised_up_to_the_cap() -> None:
    """A sample pilot destroyed 159 friendly objects in one sortie; that must not bury their whole career."""
    assert score_sortie(facts(friendly_kills=159)).air == -(5 * 3)
    assert score_sortie(facts(friendly_kills=2)).air == -6
    assert score_sortie(facts(friendly_kills=159), ScoreRules(penalty_friendly_kill_cap=10)).air == -30
