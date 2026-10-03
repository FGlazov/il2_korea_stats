"""Stat marks (FR-WEB-22): percentiles, bands and the `{% stat_mark %}` tag, without a database."""

import pytest
from django.template import Context, Template

from il2ks.core.stat_marks import (
    MIN_POPULATION,
    Thresholds,
    Totals,
    band,
    metric_value,
    percentile,
    thresholds,
)
from il2ks.db.models import Player, StatThreshold

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
