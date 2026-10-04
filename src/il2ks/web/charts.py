"""Bar charts as server-side inline SVG (FR-WEB-16): the pure layout.

`build_bar_chart(spec)` turns a `ChartSpec` (categories, one or more series of counts) into a `BarChart`: pixel
positions of the bars, the y ticks, the x labels and the rows of the "show the numbers" table. No Django, no HTML: the
`{% bar_chart spec %}` tag (`components/bar_chart.html`) only prints it. All colours and text styles are CSS (the
`--il2-chart-*` tokens in `site.css`), so charts follow light and dark mode.

Rules the layout keeps (they are tested in `tests/unit/test_charts.py`):

- Values are counts (non-negative integers). The y axis starts at 0 and ends on a "nice" tick (1, 2, 5 x 10^n steps).
- A nonzero value is always at least `MIN_BAR_PX` high, so a 1 next to a 5000 stays visible; a zero draws nothing.
- Bars are thin (`MAX_BAR_PX`), a 2 px gap apart within a category, with the data end rounded (`ROUND_PX`) and the base
  flat on the axis. One category or one huge value never makes a wide slab or an overflow.
- At most one x label per `label_width` pixels (every n-th category), long labels are cut with an ellipsis; the full
  text stays in the tooltips and the table.
- A series with `plotted=False` appears only in the table (e.g. the sorties next to a kills chart).
"""

import math
from dataclasses import dataclass

WIDTH = 560
HEIGHT = 180
MARGIN_TOP = 8
MARGIN_RIGHT = 6
MARGIN_BOTTOM = 22
MARGIN_LEFT_BASE = 10
TICK_CHAR_PX = 6.5  # rough width of one tick-label character at the CSS font size
TARGET_TICKS = 4
MAX_BAR_PX = 28.0
SERIES_GAP_PX = 2.0
BAND_FILL = 0.8  # share of a category's width its bars may use
MIN_BAR_PX = 2.0
ROUND_PX = 4.0
DEFAULT_LABEL_WIDTH = 52
MAX_LABEL_CHARS = 14


@dataclass(frozen=True, slots=True)
class ChartSeries:
    """One measure: a label and one count per category. `plotted=False` keeps it out of the bars (table only)."""

    label: str
    values: tuple[int, ...]
    plotted: bool = True


@dataclass(frozen=True, slots=True)
class ChartSpec:
    """What a chart shows. `chart_id` must be unique on the page (it names the SVG's title and description ids);
    `desc` is a sentence for screen readers (the table holds the numbers); `label_width` is the room one x label
    needs, in viewBox pixels (wider for long labels)."""

    chart_id: str
    title: str
    desc: str
    categories: tuple[str, ...]
    series: tuple[ChartSeries, ...]
    label_width: int = DEFAULT_LABEL_WIDTH


@dataclass(frozen=True, slots=True)
class Bar:
    series: int  # index among the plotted series (the CSS class `chart__bar--s<n>`)
    category: int
    value: int
    path: str  # SVG path data
    tip: str  # "category - series: value", the native tooltip


@dataclass(frozen=True, slots=True)
class YTick:
    value: int
    y: float
    label: str


@dataclass(frozen=True, slots=True)
class XLabel:
    x: float
    text: str


@dataclass(frozen=True, slots=True)
class BarChart:
    width: int
    height: int
    plot_left: float
    plot_right: float
    baseline: float
    bars: tuple[Bar, ...]
    y_ticks: tuple[YTick, ...]
    x_labels: tuple[XLabel, ...]
    legend: tuple[str, ...]  # labels of the plotted series, only when there are two or more (a lone one is the title)
    table_head: tuple[str, ...]  # the series labels, all of them
    table_rows: tuple[tuple[str, tuple[int, ...]], ...]  # (category, one value per series)
    is_empty: bool  # nothing to draw: no categories, no plotted series, or only zeros


def nice_ticks(max_value: int, target: int = TARGET_TICKS) -> tuple[int, ...]:
    """Whole-number ticks from 0 up to a nice top that is >= `max_value`: a step of 1, 2 or 5 x 10^n giving about
    `target` intervals. `max_value <= 0` gives (0, 1): an empty axis still has a scale."""
    if max_value <= 0:
        return (0, 1)
    raw = max_value / target
    magnitude = 10 ** max(math.ceil(math.log10(raw)) - 1, 0) if raw > 1 else 1
    step = next(m * magnitude for m in (1, 2, 5, 10) if m * magnitude >= raw)
    top = step * math.ceil(max_value / step)
    return tuple(range(0, top + 1, step))


def compact(value: int) -> str:
    """A short axis label: 950, 1.2k, 40k, 3M, 2.5B."""
    for limit, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
        if value >= limit:
            text = f"{value / limit:.1f}".removesuffix(".0")
            return f"{text}{suffix}"
    return str(value)


def _clip(text: str) -> str:
    return text if len(text) <= MAX_LABEL_CHARS else text[: MAX_LABEL_CHARS - 1] + "…"


def _bar_path(x: float, y: float, width: float, height: float) -> str:
    """A bar with the data end (top) rounded and the base flat."""
    r = min(ROUND_PX, width / 2, height)
    right, bottom = x + width, y + height
    return (
        f"M{x:.1f},{bottom:.1f}V{y + r:.1f}Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f}H{right - r:.1f}"
        f"Q{right:.1f},{y:.1f} {right:.1f},{y + r:.1f}V{bottom:.1f}Z"
    )


def build_bar_chart(spec: ChartSpec, width: int = WIDTH, height: int = HEIGHT) -> BarChart:
    """Lay out `spec` in a `width` x `height` viewBox. Raises ValueError for a series whose length differs from the
    number of categories or that holds a negative value."""
    count = len(spec.categories)
    for series in spec.series:
        if len(series.values) != count:
            raise ValueError(f"series {series.label!r} has {len(series.values)} values for {count} categories")
        if any(v < 0 for v in series.values):
            raise ValueError(f"series {series.label!r} has a negative value")
    plotted = [s for s in spec.series if s.plotted]
    top_value = max((v for s in plotted for v in s.values), default=0)
    ticks = nice_ticks(top_value)
    top = ticks[-1]
    labels = [compact(t) for t in ticks]
    left = MARGIN_LEFT_BASE + TICK_CHAR_PX * max(len(label) for label in labels)
    right = width - MARGIN_RIGHT
    baseline = float(height - MARGIN_BOTTOM)
    plot_h = baseline - MARGIN_TOP
    plot_w = right - left
    y_ticks = tuple(YTick(t, baseline - plot_h * t / top, label) for t, label in zip(ticks, labels, strict=True))

    table_rows = tuple((cat, tuple(s.values[i] for s in spec.series)) for i, cat in enumerate(spec.categories))
    is_empty = count == 0 or top_value == 0
    bars: list[Bar] = []
    x_labels: list[XLabel] = []
    if not is_empty:
        band = plot_w / count
        n = len(plotted)
        bar_w = max(min(MAX_BAR_PX, (band * BAND_FILL - SERIES_GAP_PX * (n - 1)) / n), 1.0)
        group_w = n * bar_w + SERIES_GAP_PX * (n - 1)
        for c, category in enumerate(spec.categories):
            group_x = left + band * c + (band - group_w) / 2
            for s, series in enumerate(plotted):
                value = series.values[c]
                if value == 0:
                    continue
                bar_h = min(max(plot_h * value / top, MIN_BAR_PX), plot_h)
                x = group_x + s * (bar_w + SERIES_GAP_PX)
                bars.append(
                    Bar(
                        s,
                        c,
                        value,
                        _bar_path(x, baseline - bar_h, bar_w, bar_h),
                        f"{category} - {series.label}: {value}",
                    )
                )
        every = max(math.ceil(count / max(int(plot_w // spec.label_width), 1)), 1)
        x_labels = [XLabel(left + band * (c + 0.5), _clip(spec.categories[c])) for c in range(0, count, every)]
    return BarChart(
        width=width,
        height=height,
        plot_left=left,
        plot_right=right,
        baseline=baseline,
        bars=tuple(bars),
        y_ticks=y_ticks,
        x_labels=tuple(x_labels),
        legend=tuple(s.label for s in plotted) if len(plotted) >= 2 else (),
        table_head=tuple(s.label for s in spec.series),
        table_rows=table_rows,
        is_empty=is_empty,
    )
