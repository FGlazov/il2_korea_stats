"""Dev-only style guide at /_styleguide/: every UI component with fake data, light and dark (404 unless DEBUG).

Doubles as the reference for page authors: it exercises the real sort, filter and pagination components against an
in-memory list, with the same view pattern a real list page uses (whitelist sort fields, resolve `sort`, paginate).
All data is invented here; nothing is read from the database.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse

from il2ks.web import display
from il2ks.web.charts import ChartSeries, ChartSpec

AIRCRAFT = ("F-86F-30 Sabre", "MiG-15bis", "F-51D-30 Mustang", "Yak-9P", "La-9", "Il-10")
SIDES = {"redfor": 501, "blufor": 601}
OUTCOMES = tuple(display.OUTCOMES)
ADJECTIVES = ("Quiet", "Rapid", "Iron", "Pale", "Silent", "Brave", "Lucky", "Steady", "Crimson", "Frozen")
NOUNS = ("Heron", "Badger", "Falcon", "Otter", "Viper", "Comet", "Raven", "Lynx", "Walrus", "Kestrel")
SORT_FIELDS = ("callsign", "aircraft", "sorties", "kills", "deaths", "flight_time_s")
DEFAULT_SORT = "-kills"
PAGE_SIZE = 12


@dataclass(frozen=True, slots=True)
class FakeRow:
    callsign: str
    country: int
    aircraft: str
    outcome: str
    sorties: int
    kills: int
    deaths: int
    flight_time_s: int
    last_seen: datetime


def fake_rows() -> list[FakeRow]:
    """140 deterministic rows from a tiny linear congruential generator, so screenshots are reproducible."""
    state = 20261003
    rows: list[FakeRow] = []
    base = datetime(2026, 9, 19, 22, 34, tzinfo=UTC)
    for index in range(140):
        state = (state * 1103515245 + 12345) % 2**31
        sorties = 3 + state % 90
        state = (state * 1103515245 + 12345) % 2**31
        rows.append(
            FakeRow(
                callsign=f"{ADJECTIVES[index % 10]} {NOUNS[(index * 7 + 3) % 10]} {index // 10 + 1}",
                country=501 if index % 3 else 601,
                aircraft=AIRCRAFT[(index * 5) % len(AIRCRAFT)],
                outcome=OUTCOMES[(index * 3) % len(OUTCOMES)],
                sorties=sorties,
                kills=state % (sorties * 3),
                deaths=state % (sorties + 1) // 2,
                flight_time_s=sorties * (1200 + state % 2400),
                last_seen=base - timedelta(hours=index * 7),
            )
        )
    return rows


def icon_names() -> list[str]:
    """Every built-in icon, as 'category/name', for the icon gallery (illustrations and patterns are not icons)."""
    root = Path(__file__).resolve().parents[1] / "static" / "il2ks" / "img"
    skipped = {"pattern", "illustration"}
    return sorted(
        path.relative_to(root).with_suffix("").as_posix()
        for path in root.rglob("*.svg")
        if path.relative_to(root).parts[0] not in skipped
    )


def sample_charts() -> list[ChartSpec]:
    """Fake charts for the style guide: two series, one series with a huge value, one point, and an empty one."""
    months = ("May", "Jun", "Jul", "Aug", "Sep", "Oct")
    return [
        ChartSpec(
            "sg-chart-two",
            "Air kills and deaths per tour",
            "Two series over six tours.",
            months,
            (ChartSeries("Air kills", (12, 40, 31, 57, 18, 3)), ChartSeries("Deaths", (9, 22, 35, 41, 10, 4))),
        ),
        ChartSpec(
            "sg-chart-big",
            "Huge values and tiny ones",
            "One series, the second bar is 1 of 48,000.",
            months,
            (ChartSeries("Sorties", (48_000, 1, 0, 12_345, 700, 25_000)),),
        ),
        ChartSpec("sg-chart-one", "A single point", "One category.", ("Sep",), (ChartSeries("Sorties", (7,)),)),
        ChartSpec("sg-chart-empty", "No data yet", "Nothing.", months, (ChartSeries("Sorties", (0,) * 6),)),
    ]


def resolve_sort(raw: str) -> str:
    """The whitelisted sort for a ?sort= value: unknown fields fall back to the default, never an error."""
    return raw if raw.removeprefix("-") in SORT_FIELDS else DEFAULT_SORT


def styleguide(request: HttpRequest) -> HttpResponse:
    if not settings.DEBUG:
        raise Http404
    sort = resolve_sort(request.GET.get("sort", ""))
    aircraft = request.GET.get("aircraft", "")
    side = request.GET.get("side", "")
    name = request.GET.get("q", "").strip().lower()

    rows = [
        row
        for row in fake_rows()
        if (not aircraft or row.aircraft == aircraft)
        and (not side or display.side_of(row.country) == side)
        and (not name or name in row.callsign.lower())
    ]
    rows.sort(key=lambda row: getattr(row, sort.removeprefix("-")), reverse=sort.startswith("-"))
    page = Paginator(rows, PAGE_SIZE).get_page(request.GET.get("page"))

    context: dict[str, object] = {
        "page_title": "Style guide",
        "page_obj": page,
        "rows": page.object_list,
        "sort": sort,
        "aircraft_options": [(name_, name_) for name_ in AIRCRAFT],
        "side_options": [(key, key.upper()) for key in SIDES],
        "outcomes": list(display.OUTCOMES),
        "fates": list(display.FATES),
        "statuses": list(display.STATUSES),
        "roles": list(display.ROLES),
        "aircraft_statuses": list(display.AIRCRAFT_STATUSES),
        "icon_names": icon_names(),
        "charts": sample_charts(),
        "sample_aircraft": [
            SimpleNamespace(log_name="MiG-15bis", propulsion="jet"),
            SimpleNamespace(log_name="Yak-9P", propulsion="prop"),
            SimpleNamespace(log_name="Mystery type", propulsion=""),
        ],
        "crumbs": [("Players", reverse("web:player-search")), ("Quiet Heron 1", None)],
        "menu_items": [("First entry", "#"), ("Second entry", "#"), ("Third entry", "#")],
        "sample_seconds": [45, 720, 4980, 7200],
        "sample_when": datetime(2026, 9, 19, 22, 34, tzinfo=UTC),
        "kv_rows": [("Aircraft", "F-86F-30 Sabre"), ("Spawned", "2026-09-19 22:34 UTC"), ("Flight time", "1 h 23 min")],
    }
    return render(request, "il2ks/styleguide.html", context)
