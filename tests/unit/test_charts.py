"""The pure bar-chart layout (FR-WEB-16): scales, ticks, empty data, one point, huge values."""

import re
from itertools import pairwise

import pytest

from il2ks.web.charts import MAX_BAR_PX, MIN_BAR_PX, ChartSeries, ChartSpec, build_bar_chart, compact, nice_ticks


def spec(*series: ChartSeries, categories: tuple[str, ...] | None = None, label_width: int = 52) -> ChartSpec:
    cats = categories if categories is not None else tuple(f"c{i}" for i in range(len(series[0].values)))
    return ChartSpec("chart-x", "Title", "Desc", cats, series, label_width)


def points(path: str) -> list[tuple[float, float]]:
    """The x,y pairs of a bar's path (M and Q commands)."""
    return [(float(x), float(y)) for x, y in re.findall(r"(-?[\d.]+),(-?[\d.]+)", path)]


def top_of(path: str) -> float:
    """The smallest y in a bar's path (its data end)."""
    return min(y for _, y in points(path))


@pytest.mark.parametrize(
    ("max_value", "expected"),
    [
        (0, (0, 1)),
        (-5, (0, 1)),
        (1, (0, 1)),
        (3, (0, 1, 2, 3)),
        (7, (0, 2, 4, 6, 8)),
        (10, (0, 5, 10)),
        (57, (0, 20, 40, 60)),
        (1000, (0, 500, 1000)),
        (1001, (0, 500, 1000, 1500)),
    ],
)
def test_nice_ticks(max_value: int, expected: tuple[int, ...]) -> None:
    assert nice_ticks(max_value) == expected


@pytest.mark.parametrize("max_value", [1, 2, 9, 13, 99, 480, 12_345, 48_000, 10**9, 7 * 10**12])
def test_nice_ticks_cover_the_max_with_few_ticks(max_value: int) -> None:
    ticks = nice_ticks(max_value)

    assert ticks[0] == 0
    assert ticks[-1] >= max_value
    assert len(ticks) <= 8
    assert len({b - a for a, b in pairwise(ticks)}) == 1  # even steps


@pytest.mark.parametrize(
    ("value", "text"),
    [(0, "0"), (950, "950"), (1000, "1k"), (1234, "1.2k"), (40_000, "40k"), (3_000_000, "3M"), (2_500_000_000, "2.5B")],
)
def test_compact(value: int, text: str) -> None:
    assert compact(value) == text


def test_bars_sit_on_the_baseline_and_scale_with_the_value() -> None:
    chart = build_bar_chart(spec(ChartSeries("Kills", (10, 5, 0, 20))))

    assert [bar.value for bar in chart.bars] == [10, 5, 20]  # a zero draws nothing
    heights = {bar.value: chart.baseline - top_of(bar.path) for bar in chart.bars}
    assert heights[20] > heights[10] > heights[5]
    assert heights[20] == pytest.approx(heights[10] * 2, rel=0.01)
    assert chart.y_ticks[0].y == chart.baseline
    assert chart.y_ticks[-1].value == 20  # the top tick is the nice max
    assert not chart.is_empty


def test_empty_data_has_no_bars_but_still_a_scale() -> None:
    for chart in (
        build_bar_chart(spec(ChartSeries("Kills", ()), categories=())),
        build_bar_chart(spec(ChartSeries("Kills", (0, 0, 0)))),
    ):
        assert chart.is_empty
        assert chart.bars == ()
        assert chart.x_labels == ()
        assert chart.y_ticks[0].value == 0


def test_only_table_series_and_no_plotted_series_is_empty() -> None:
    chart = build_bar_chart(spec(ChartSeries("Pilots", (3, 4), plotted=False)))

    assert chart.is_empty
    assert chart.table_head == ("Pilots",)


def test_one_point_is_a_single_thin_centred_bar() -> None:
    chart = build_bar_chart(spec(ChartSeries("Sorties", (7,)), categories=("Sep",)))

    assert len(chart.bars) == 1
    assert len(chart.x_labels) == 1
    centre = (chart.plot_left + chart.plot_right) / 2
    assert chart.x_labels[0].x == pytest.approx(centre)
    xs = [x for x, _ in points(chart.bars[0].path)]
    assert max(xs) - min(xs) <= MAX_BAR_PX + 0.1  # never a slab across the whole width
    assert min(xs) < centre < max(xs)


def test_huge_values_stay_inside_the_plot_and_a_one_stays_visible() -> None:
    chart = build_bar_chart(spec(ChartSeries("Sorties", (48_000_000_000, 1))))

    tops = {bar.value: top_of(bar.path) for bar in chart.bars}
    assert tops[48_000_000_000] >= 8  # inside the top margin
    assert chart.baseline - tops[1] >= MIN_BAR_PX - 0.1
    assert chart.y_ticks[-1].label.endswith("B")
    assert chart.plot_left < 70  # the long tick labels don't eat the plot


def test_two_series_are_grouped_side_by_side_with_a_legend() -> None:
    chart = build_bar_chart(spec(ChartSeries("Kills", (3, 4)), ChartSeries("Deaths", (1, 2))))

    assert chart.legend == ("Kills", "Deaths")
    first, second = (b for b in chart.bars if b.category == 0)
    assert (first.series, second.series) == (0, 1)
    assert first.tip == "c0 - Kills: 3"


def test_one_series_has_no_legend() -> None:
    assert build_bar_chart(spec(ChartSeries("Kills", (3, 4)))).legend == ()


def test_table_only_series_are_in_the_table_but_not_the_bars() -> None:
    chart = build_bar_chart(spec(ChartSeries("Sorties", (5, 6)), ChartSeries("Pilots", (2, 3), plotted=False)))

    assert {bar.series for bar in chart.bars} == {0}
    assert chart.table_head == ("Sorties", "Pilots")
    assert chart.table_rows == (("c0", (5, 2)), ("c1", (6, 3)))
    assert chart.legend == ()


def test_x_labels_are_thinned_and_clipped() -> None:
    cats = tuple(f"Day number {i} of the long month" for i in range(30))
    chart = build_bar_chart(spec(ChartSeries("S", tuple(range(1, 31))), categories=cats))

    assert 2 <= len(chart.x_labels) <= 11
    assert all(len(label.text) <= 14 for label in chart.x_labels)
    assert chart.x_labels[0].text.endswith("…")
    assert chart.table_rows[0][0] == cats[0]  # the table keeps the full text


def test_labels_do_not_overlap() -> None:
    chart = build_bar_chart(spec(ChartSeries("S", tuple(range(1, 31))), label_width=52))

    xs = [label.x for label in chart.x_labels]
    assert all(b - a >= 52 for a, b in pairwise(xs))


def test_mismatched_or_negative_series_are_rejected() -> None:
    with pytest.raises(ValueError, match="values for"):
        build_bar_chart(ChartSpec("x", "T", "D", ("a", "b"), (ChartSeries("S", (1,)),)))
    with pytest.raises(ValueError, match="negative"):
        build_bar_chart(spec(ChartSeries("S", (1, -2))))
