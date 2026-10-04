"""Air and ground score of one sortie (FR-WEB-7, FR-ADM-7)."""

from il2ks.core.catalog.loader import GroundCategory
from il2ks.core.ratings.score import ScoreRules, SortieFacts, SortieScore, score_sortie

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


def test_a_death_costs_80_percent_of_both_scores() -> None:
    """OQ-62/63: percentages of the sortie's score by outcome (death 80%)."""
    result = score_sortie(facts(kills_air_pvp=2, kills_ground={"tank": 1}, is_death=True, is_plane_lost=True))
    assert result == SortieScore(air=4.0, ground=1.2)


def test_a_capture_costs_50_percent_and_a_lost_plane_alone_20_percent() -> None:
    captured = score_sortie(facts(kills_air_pvp=1, is_captured=True, is_plane_lost=True))
    assert captured == SortieScore(air=5.0, ground=0.0)
    ditched = score_sortie(facts(kills_air_pvp=1, is_plane_lost=True))  # OQ-67 (a)
    assert ditched == SortieScore(air=8.0, ground=0.0)


def test_outcomes_do_not_add_up_the_largest_percentage_counts() -> None:
    """OQ-67: a death, a capture and the lost aircraft together cost 80%, not 150%."""
    result = score_sortie(facts(kills_air_pvp=1, is_death=True, is_captured=True, is_plane_lost=True))
    assert result.air == 2.0


def test_a_percentage_never_makes_a_score_negative() -> None:
    assert score_sortie(facts(is_death=True, is_plane_lost=True)) == SortieScore(0.0, 0.0)
    assert score_sortie(facts(attack=True, is_captured=True, is_plane_lost=True)) == SortieScore(0.0, 0.0)


def test_flat_penalties_come_off_after_the_percentage_from_the_role_score() -> None:
    fighter = score_sortie(
        facts(kills_air_pvp=1, is_death=True, is_plane_lost=True, suspected_early_bailout=True, friendly_kills=1)
    )
    assert fighter == SortieScore(air=10 * 0.2 - 5 - 3, ground=0.0)
    attacker = score_sortie(
        facts(
            attack=True,
            kills_air_pvp=1,
            kills_ground={"tank": 2},
            is_death=True,
            is_plane_lost=True,
            suspected_early_bailout=True,
        )
    )
    assert attacker == SortieScore(air=2.0, ground=-2.6)


def test_a_surviving_sortie_loses_nothing_to_the_percentages() -> None:
    assert score_sortie(facts(kills_air_pvp=1, kills_ground={"tank": 1})) == SortieScore(10.0, 6.0)


def test_friendly_kills_are_penalised_up_to_the_cap() -> None:
    """A sample pilot destroyed 159 friendly objects in one sortie; that must not bury their whole career."""
    assert score_sortie(facts(friendly_kills=159)).air == -(5 * 3)
    assert score_sortie(facts(friendly_kills=2)).air == -6
    assert score_sortie(facts(friendly_kills=159), ScoreRules(penalty_friendly_kill_cap=10)).air == -30
