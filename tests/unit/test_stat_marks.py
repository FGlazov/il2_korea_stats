"""Stat marks (FR-WEB-22): percentiles, bands and the `{% stat_mark %}` tag, without a database."""

from dataclasses import replace

import pytest
from django.template import Context, Template

from il2ks.core.stat_marks import (
    METRICS,
    MIN_POPULATION,
    MarkRules,
    Thresholds,
    Totals,
    amount,
    band,
    metric_value,
    percentile,
    thresholds,
)
from il2ks.db.models import Player, PlayerTour, StatThreshold

LIMITS = Thresholds(p10=0.1, p25=0.2, p50=0.5, p75=0.7, p90=0.9, population=100)


# --- percentiles ---------------------------------------------------------------------------------------------------
def test_percentile_interpolates_between_neighbours() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentile(values, 0) == 1.0
    assert percentile(values, 50) == 3.0
    assert percentile(values, 100) == 5.0
    assert percentile(values, 25) == 2.0
    assert percentile([1.0, 2.0], 90) == pytest.approx(1.9)


def test_percentile_of_one_value_and_of_nothing() -> None:
    assert percentile([7.0], 90) == 7.0
    with pytest.raises(ValueError, match="nothing"):
        percentile([], 50)


def test_all_equal_values_give_equal_thresholds() -> None:
    found = thresholds([0.5] * MIN_POPULATION)
    assert found == Thresholds(0.5, 0.5, 0.5, 0.5, 0.5, MIN_POPULATION)


def test_ties_and_undefined_values() -> None:
    values: list[float | None] = [0.0] * 15 + [1.0] * 10 + [None] * 5
    found = thresholds(values)
    assert found is not None
    assert found.population == 25  # undefined values are left out
    assert (found.p10, found.p50, found.p90) == (0.0, 0.0, 1.0)


def test_small_population_has_no_thresholds() -> None:
    assert thresholds([float(i) for i in range(MIN_POPULATION - 1)]) is None
    assert thresholds([float(i) for i in range(MIN_POPULATION)]) is not None
    assert thresholds([None] * 50) is None


def test_unsorted_input_is_fine() -> None:
    found = thresholds([float(i) for i in reversed(range(MIN_POPULATION))])
    assert found is not None
    assert found.p50 == pytest.approx((MIN_POPULATION - 1) / 2)


# --- metrics -------------------------------------------------------------------------------------------------------
TOTALS = Totals(sorties=40, deaths=10, planes_lost=8, kills_air=20, kills_ground=60, flight_time_s=7200.0)


def test_metric_values_follow_the_page_ratios() -> None:
    assert metric_value("survival", TOTALS) == 0.75
    assert metric_value("kd", TOTALS) == 2.0
    assert metric_value("kl", TOTALS) == 2.5
    assert metric_value("air_per_sortie", TOTALS) == 0.5
    assert metric_value("air_per_hour", TOTALS) == 10.0
    assert metric_value("ground_per_sortie", TOTALS) == 1.5


def test_incident_rates_are_per_sortie() -> None:
    assert metric_value("taxi_per_sortie", TOTALS) == 0.0
    busy = Totals(40, 0, 0, 0, 0, 0.0, taxi_accidents=4, friendly_fire_incidents=2)
    assert metric_value("taxi_per_sortie", busy) == 0.1
    assert metric_value("friendly_fire_per_sortie", busy) == 0.05
    assert metric_value("friendly_fire_per_sortie", Totals(0, 0, 0, 0, 0, 0.0)) is None


def test_undefined_where_the_page_shows_a_dash() -> None:
    flawless = Totals(sorties=30, deaths=0, planes_lost=0, kills_air=5, kills_ground=0, flight_time_s=0.0)
    assert metric_value("kd", flawless) is None
    assert metric_value("kl", flawless) is None
    assert metric_value("air_per_hour", flawless) is None
    assert metric_value("survival", flawless) == 1.0


# --- bands ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.95, "top"), (0.9, "high"), (0.8, "high"), (0.7, None), (0.5, None), (0.0, None), (None, None)],
)
def test_band_is_strictly_above_the_threshold(value: float | None, expected: str | None) -> None:
    """At the threshold itself nobody is marked: the claim "better than 9 in 10" must stay true with ties."""
    assert band(value, LIMITS) == expected


def test_low_values_are_never_marked() -> None:
    assert band(0.0, LIMITS) is None
    assert band(LIMITS.p10 - 0.05, LIMITS) is None


# --- the template tag ----------------------------------------------------------------------------------------------
def _threshold(metric: str, **overrides: float) -> StatThreshold:
    values = {"p10": 0.1, "p25": 0.2, "p50": 0.5, "p75": 0.7, "p90": 0.9, **overrides}
    return StatThreshold(metric=metric, min_sorties=20, population=100, **values)


def _render(source: str, player: Player, marks: dict[str, StatThreshold]) -> str:
    return Template("{% load il2ks %}" + source).render(Context({"stats": player, "marks": marks})).strip()


def _player(sorties: int, deaths: int) -> Player:
    return Player(sorties=sorties, deaths=deaths)


MARKS = {"survival": _threshold("survival")}


def test_top_band_renders_badge_with_text_and_tooltip() -> None:
    html = _render('{% stat_mark "survival" %}', _player(sorties=100, deaths=2), MARKS)  # 98% survival
    assert "stat-mark--top" in html
    assert "Top 10%" in html
    assert "Better than 9 in 10 pilots with at least 20 sorties" in html


def test_high_band_renders_the_quieter_badge() -> None:
    html = _render('{% stat_mark "survival" %}', _player(sorties=100, deaths=20), MARKS)  # 80%
    assert "stat-mark--high" in html
    assert "Top 25%" in html
    assert "Better than 3 in 4 pilots" in html


def test_middle_and_low_values_render_nothing() -> None:
    assert _render('{% stat_mark "survival" %}', _player(sorties=100, deaths=50), MARKS) == ""  # 50%
    assert _render('{% stat_mark "survival" %}', _player(sorties=100, deaths=95), MARKS) == ""  # 5%: never shamed


def test_under_the_minimum_gets_no_mark_but_a_note() -> None:
    player = _player(sorties=5, deaths=0)
    assert _render('{% stat_mark "survival" %}', player, MARKS) == ""
    note = _render("{% stat_mark_note %}", player, MARKS)
    assert "from 20 sorties on" in note
    assert _render("{% stat_mark_note %}", _player(sorties=50, deaths=0), MARKS) == ""


def test_no_thresholds_no_marks_no_note() -> None:
    player = _player(sorties=100, deaths=0)
    assert _render('{% stat_mark "survival" %}', player, {}) == ""
    assert _render("{% stat_mark_note %}", player, {}) == ""
    assert _render('{% stat_mark "kd" %}', player, MARKS) == ""  # this metric has no row


def test_no_stats_renders_nothing() -> None:
    html = Template('{% load il2ks %}{% stat_mark "survival" %}{% stat_mark_note %}').render(
        Context({"stats": None, "marks": MARKS})
    )
    assert html.strip() == ""


# --- score and Elo marks (2026-10-04) --------------------------------------------------------------------------------
def test_score_and_elo_metric_values() -> None:
    totals = Totals(
        sorties=10,
        deaths=0,
        planes_lost=0,
        kills_air=0,
        kills_ground=0,
        flight_time_s=0,
        score_air=120.0,
        score_ground=80.0,
        score_ground_attack=60.0,
        time_on_target_s=1800.0,
        elo_prop=1620.0,
        elo_prop_games=7,
    )
    assert metric_value("air_score", totals) == 120.0
    assert metric_value("ground_score", totals) == 80.0
    assert metric_value("ground_score_hour", totals) == 120.0
    assert metric_value("elo_prop", totals) == 1620.0
    assert metric_value("elo_jet", totals) is None  # no rated jet games: the dash
    assert metric_value("ground_score_hour", replace(totals, time_on_target_s=0.0)) is None


def test_every_metric_key_fits_the_column() -> None:
    assert all(len(metric) <= 24 for metric in METRICS)


def test_each_metric_has_its_own_minimum() -> None:
    rules = MarkRules(min_sorties=20, min_elo_games=5, min_time_on_target_s=600.0)
    assert rules.minimum("air_score") == 20
    assert (rules.minimum("elo_jet"), rules.minimum("elo_prop")) == (5, 5)
    assert rules.minimum("ground_score_hour") == 600
    assert MarkRules(min_elo_games=0).minimum("elo_jet") == 1  # like the boards: at least one rated game
    totals = Totals(0, 0, 0, 0, 0, 0.0, time_on_target_s=599.0, elo_jet_games=5)
    assert amount("ground_score_hour", totals) < rules.minimum("ground_score_hour")
    assert amount("elo_jet", totals) >= rules.minimum("elo_jet")
    assert amount("elo_prop", totals) < rules.minimum("elo_prop")


def test_elo_mark_uses_the_player_also_when_stats_is_a_tour_row() -> None:
    marks = {"elo_jet": _threshold("elo_jet", p10=1400.0, p25=1450.0, p50=1500.0, p75=1550.0, p90=1600.0)}
    marks["elo_jet"].min_sorties = 5  # encounters for an Elo row
    player = Player(sorties=1, elo_jet=1700.0, elo_jet_games=6)
    source = '{% load il2ks %}{% stat_mark "elo_jet" %}'
    html = Template(source).render(Context({"stats": PlayerTour(sorties=1), "player": player, "marks": marks}))
    assert "Top 10%" in html
    assert "at least 5 encounters" in html
    few = Player(sorties=1, elo_jet=1700.0, elo_jet_games=4)
    assert Template(source).render(Context({"stats": few, "player": few, "marks": marks})).strip() == ""


def test_ground_score_hour_mark_needs_the_time_on_target() -> None:
    marks = {"ground_score_hour": _threshold("ground_score_hour", p10=10.0, p25=20.0, p50=30.0, p75=40.0, p90=50.0)}
    marks["ground_score_hour"].min_sorties = 600  # seconds on target
    source = '{% load il2ks %}{% stat_mark "ground_score_hour" %}'
    good = Player(sorties=1, score_ground_attack=100.0, time_on_target_s=1800.0)  # 200 per hour
    assert "at least 10 minutes on target" in Template(source).render(Context({"stats": good, "marks": marks}))
    brief = Player(sorties=1, score_ground_attack=100.0, time_on_target_s=300.0)
    assert Template(source).render(Context({"stats": brief, "marks": marks})).strip() == ""


def test_note_ignores_elo_and_time_rows() -> None:
    elo = _threshold("elo_jet")
    elo.min_sorties = 5
    marks = {"elo_jet": elo, "air_score": _threshold("air_score")}
    assert "from 20 sorties on" in _render("{% stat_mark_note %}", _player(sorties=3, deaths=0), marks)
