"""Template filters and tags of the web UI: `{% load il2ks %}`.

The tags render the files in `templates/il2ks/components/`; every component documents its context at the top of
its template, and server owners may override any of them (TD-25). Tags that need the current URL read the request from
the context (`django.template.context_processors.request`, enabled in settings) and keep the other query parameters.

Filters (formatting only, TD-22): duration, utc, local_time, local_short, local_date, local_hm, local_clock,
num, ratio, per_hour, percent, mission_title, game_when, clock_since, tour_title; object_name (TD-24: show a GameObject
in the viewer's language, never `.display_name` directly).
Tags: icon, aircraft_icon, side, badge, coalition_badge, coalition_icon, winner_badge, outcome_badge, fate_badge,
pilot_fate_badge, status_badge, aircraft_badge, role_badge, stat_tile, kv_list, empty_row, breadcrumbs, dropdown,
language_menu, sort_th, pagination, filter_select, filter_text, tour_select, tour_filter, stat_mark,
stat_mark_note, flavor, sortie_flavor
(flavor text, FR-WEB-23), bar_chart.
Block tags: results_region, filter_bar, accordion, notice.
"""

import dataclasses
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime
from typing import Never, cast

from django import template
from django.conf import settings
from django.core.paginator import Page
from django.http import HttpRequest, QueryDict
from django.template import Context, Node, NodeList, TemplateSyntaxError
from django.template.base import FilterExpression, Parser, Token, kwarg_re
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.safestring import SafeString
from django.utils.translation import get_language, get_language_info

from il2ks.core import stat_marks
from il2ks.db.models import Counters, PlayerSortie, StatThreshold, Tour
from il2ks.queries import tours as tour_reads
from il2ks.web import columns, display, icons, object_names
from il2ks.web import flavor as flavor_text
from il2ks.web.charts import ChartSpec, build_bar_chart
from il2ks.web.display import SortFirst, Tone
from il2ks.web.flavor import stat_marks_totals

register = template.Library()

COMPONENTS = "il2ks/components/"
NON_FILTER_PARAMS = frozenset({"page", "sort", columns.COLS_PARAM})  # a column choice is not a filter

type Option = tuple[object, object]
type Crumb = tuple[object, str | None]


# --- query string helpers -----------------------------------------------------------------------------------------
def _request_of(context: Context) -> HttpRequest | None:
    request = context.get("request")
    return request if isinstance(request, HttpRequest) else None


def _params_of(context: Context) -> QueryDict:
    request = _request_of(context)
    return request.GET if request is not None else QueryDict()


def _page_resets(params: QueryDict) -> dict[str, None]:
    """Every paging parameter ('page', 'page_redfor', ...) set to None: a new sort starts every table at page 1."""
    return {key: None for key in {"page", *params} if key == "page" or key.startswith("page_")}


def replace_query(params: QueryDict, changes: Mapping[str, object | None]) -> str:
    """'?a=1&b=2' from `params` with `changes` applied: a None or '' value drops the key. '' when nothing is left.

    Page agents building links in Python use this; templates can use Django's `{% querystring %}` (same semantics)."""
    updated = params.copy()
    for key, value in changes.items():
        if value is None or value == "":
            updated.pop(key, None)
        else:
            updated[key] = str(value)
    encoded = updated.urlencode()
    return f"?{encoded}" if encoded else "?"


# --- filters ------------------------------------------------------------------------------------------------------
@register.filter
def duration(seconds: object) -> str:
    """{{ sortie.flight_time_s|duration }} -> '1 h 23 min', '12 min', '45 s'."""
    return display.duration(seconds)


@register.filter
def utc(value: datetime | None) -> str:
    """{{ mission.started_at|utc }} -> '2026-09-19 22:34 UTC'."""
    return display.utc(value)


@register.filter
def local_time(value: datetime | None) -> SafeString | str:
    """{{ mission.started_at|local_time }} -> <time> '2026-09-19 22:34 UTC', in the viewer's zone once JS runs."""
    return display.time_element(value, "datetime", suffix=True)


@register.filter
def local_short(value: datetime | None) -> SafeString | str:
    """Like local_time without the ' UTC' text, for table columns (the footer says which zone the times are in)."""
    return display.time_element(value, "datetime")


@register.filter
def local_date(value: datetime | None) -> SafeString | str:
    """{{ player.first_seen|local_date }} -> <time> '2026-09-19' (the viewer's date once JS runs)."""
    return display.time_element(value, "date")


@register.filter
def local_hm(value: datetime | None) -> SafeString | str:
    """{{ m.started_at|local_hm }} -> <time> '22:34'."""
    return display.time_element(value, "time")


@register.filter
def local_clock(value: datetime | None) -> SafeString | str:
    """{{ row.at|local_clock }} -> <time> '20:41:07'."""
    return display.time_element(value, "clock")


@register.filter
def num(value: object, places: int = 0) -> str:
    """{{ player.kills_air|num }} -> '1,234'; {{ x|num:1 }} -> one decimal."""
    return display.num(value, places)


@register.filter
def ratio(numerator: object, denominator: object) -> str:
    """{{ p.kills_air|ratio:p.deaths }} -> '2.35', or the dash when the denominator is 0 (K/D, TD-22)."""
    return display.ratio(numerator, denominator)


@register.filter
def per_hour(count: object, seconds: object) -> str:
    """{{ p.kills_air|per_hour:p.flight_time_s }} -> kills per flight hour, '2 decimals', or the dash."""
    return display.per_hour(count, seconds)


@register.filter
def percent(part: object, whole: object) -> str:
    """{{ landed|percent:sorties }} -> '87%', or the dash when `whole` is 0."""
    return display.percent(part, whole)


@register.filter
def accuracy(hits: object, rounds: object) -> str:
    """{{ s.accuracy_hits|accuracy:s.accuracy_rounds }} -> '3.4%' (gun hits per round fired), or the dash if none."""
    return display.percent(hits, rounds, 1)


@register.filter
def mission_title(mission: object) -> str:
    """{{ mission|mission_title }} -> 'The Sinuiju Bridges 1951': a readable name from the mission file path."""
    return display.mission_title(str(getattr(mission, "mission_file", "")))


@register.filter
def clock_since(when: datetime | None, start: datetime | None) -> str:
    """{{ kill.time|clock_since:mission.started_at }} -> '12:34' (or '1:02:03'): time into the mission."""
    return display.clock_since(when, start)


@register.filter
def game_when(mission: object) -> str:
    """{{ mission|game_when }} -> '1951-09-15 13:00': the in-game date and time of a mission (not real time)."""
    return display.game_when(str(getattr(mission, "game_date", "")), str(getattr(mission, "game_time", "")))


@register.filter
def tour_title(tour: Tour | str) -> str:
    """{{ tour|tour_title }}: the tour's name in the viewer's language ("October 2026" -> "Oktober 2026"; a title an
    admin renamed stays as written)."""
    return tour_reads.tour_title(tour if isinstance(tour, str) else tour.title)


@register.filter
def object_name(game_object: object) -> str:
    """{{ sortie.aircraft|object_name }} for a GameObject: its name in the viewer's language (TD-24).

    Admin override first, then the shipped translation, then the English default, then the raw log name."""
    return object_names.name_of(game_object, get_language() or "en")


# --- coalitions and badges ----------------------------------------------------------------------------------------
@register.simple_tag(takes_context=True)
def side(context: Context, country: object) -> str:
    """{% side sortie.country %} or {% side sortie.country as name %}: REDFOR/BLUFOR display name (5xx/6xx, doc 06)."""
    site = context.get("site")
    return display.side_name(
        country,
        str(getattr(site, "redfor_name", "REDFOR")),
        str(getattr(site, "blufor_name", "BLUFOR")),
    )


@register.inclusion_tag(COMPONENTS + "badge.html")
def badge(text: object, tone: Tone = "grey", title: str = "", icon: str = "") -> dict[str, object]:
    """{% badge "Verified" "green" icon="outcome/landed" %}: the generic pill. Tones: see badge.html."""
    return {"text": text, "tone": tone, "title": title, "icon_html": icons.icon_markup(icon) if icon else ""}


def _side_icon(context: Context, key: display.Side | None, css_class: str = "") -> SafeString:
    """The side's emblem as chosen in the site settings (neutral by default); the neutral one if the file is missing."""
    if key is None:
        return SafeString("")
    site = context.get("site")
    name = display.coalition_icon_name(
        key, str(getattr(site, "redfor_emblem", "")), str(getattr(site, "blufor_emblem", ""))
    )
    return icons.icon_markup(
        name if icons.icon_exists(name) else f"coalition/{key}", f"tint--{key} {css_class}".strip()
    )


@register.inclusion_tag(COMPONENTS + "badge.html", takes_context=True)
def coalition_badge(context: Context, country: object) -> dict[str, object]:
    """{% coalition_badge sortie.country %}: red-ish REDFOR or blue-ish BLUFOR pill (country code or side key).

    The icon is the side's emblem from the site settings (SiteSettings.redfor_emblem / blufor_emblem, doc 15)."""
    key = display.side_of(country)
    return {
        "text": side(context, country),
        "tone": key if key is not None else "grey",
        "icon_html": _side_icon(context, key),
    }


@register.simple_tag(takes_context=True)
def coalition_icon(context: Context, country: object, css_class: str = "") -> SafeString:
    """{% coalition_icon sortie.country %}: only the side's emblem (country code or side key), '' for neither."""
    return _side_icon(context, display.side_of(country), css_class)


@register.simple_tag(takes_context=True)
def winner_badge(context: Context, mission: object) -> SafeString:
    """{% winner_badge mission %}: the winning side's coalition badge, or a muted dash while the winner is unknown."""
    key = display.coalition_side(
        getattr(mission, "winning_coalition", None),
        getattr(mission, "countries", None),
    )
    if key is None:
        return SafeString(f'<span class="muted">{display.DASH}</span>')
    data = {"text": side(context, key), "tone": key, "icon_html": _side_icon(context, key)}
    return SafeString(render_to_string(COMPONENTS + "badge.html", data))


def _enum_badge(table: Mapping[str, display.BadgeSpec], value: object) -> dict[str, object]:
    text, tone, icon = display.badge_spec(table, value)
    return {"text": text, "tone": tone, "icon_html": icons.icon_markup(icon) if icon else ""}


@register.inclusion_tag(COMPONENTS + "badge.html")
def outcome_badge(value: object) -> dict[str, object]:
    """{% outcome_badge sortie.outcome %}: landed, ditched, crashed, shot_down, in_flight, not_taken_off, ..."""
    return _enum_badge(display.OUTCOMES, value)


@register.inclusion_tag(COMPONENTS + "badge.html")
def fate_badge(value: object) -> dict[str, object]:
    """{% fate_badge sortie.pilot_fate %}: in_aircraft, bailed_out, exited_on_ground, ..."""
    return _enum_badge(display.FATES, value)


@register.inclusion_tag(COMPONENTS + "pilot_fate.html")
def pilot_fate_badge(sortie: PlayerSortie, detail: bool = False) -> dict[str, object]:
    """{% pilot_fate_badge sortie %}: Dead, Captured or Survived (dead > captured > the rest).
    Unknown counts as survived. The stored fate (bailed out, ...) is the badge's tooltip,
    or with `detail=True` is written next to it."""
    text, tone, icon = display.badge_spec(display.PILOT_FATES, display.pilot_fate_key(sortie))
    return {
        "text": text,
        "tone": tone,
        "icon_html": icons.icon_markup(icon) if icon else "",
        "detail": display.pilot_fate_detail(sortie),
        "show_detail": detail,
    }


@register.inclusion_tag(COMPONENTS + "badge.html")
def status_badge(value: object) -> dict[str, object]:
    """{% status_badge sortie.pilot_status %}: healthy, wounded, dead, captured."""
    return _enum_badge(display.STATUSES, value)


@register.inclusion_tag(COMPONENTS + "badge.html")
def aircraft_badge(value: object) -> dict[str, object]:
    """{% aircraft_badge sortie.aircraft_status %}: unharmed, damaged, destroyed (never derive it from damage_taken)."""
    return _enum_badge(display.AIRCRAFT_STATUSES, value)


@register.inclusion_tag(COMPONENTS + "badge.html")
def role_badge(value: object) -> dict[str, object]:
    """{% role_badge sortie.combat_role %}: air_superiority, attack."""
    return _enum_badge(display.ROLES, value)


# --- small components ---------------------------------------------------------------------------------------------
@register.inclusion_tag(COMPONENTS + "stat_tile.html")
def stat_tile(value: object, label: object, sub: object = "", icon: str = "") -> dict[str, object]:
    """{% stat_tile p.kills_air|num _("Air kills") sub=kd icon="stat/air-kills" %}; wrap tiles in .stat-tiles."""
    return {"value": value, "label": label, "sub": sub, "icon_html": icons.icon_markup(icon) if icon else ""}


@register.simple_tag
def icon(name: str, css_class: str = "") -> SafeString:
    """{% icon "event/takeoff" %} or {% icon "stat/sorties" "my-class" %}: the inline SVG from static/il2ks/img/.

    It follows the text colour (currentColor) and renders nothing when the file does not exist."""
    return icons.icon_markup(name, css_class)


@register.simple_tag
def aircraft_icon(aircraft: object, css_class: str = "") -> SafeString:
    """{% aircraft_icon sortie.aircraft %} for a GameObject: aircraft/<log_name slug>.svg, else the generic jet or prop
    (by `propulsion`), else a question-mark aircraft."""
    name = icons.aircraft_icon_name(str(getattr(aircraft, "log_name", "")), str(getattr(aircraft, "propulsion", "")))
    return icons.icon_markup(name, css_class)


@register.inclusion_tag(COMPONENTS + "kv_list.html")
def kv_list(items: Iterable[tuple[object, object]]) -> dict[str, object]:
    """{% kv_list rows %}: a summary list from (label, value) pairs; values may be markup (badges)."""
    return {"items": list(items)}


@register.inclusion_tag(COMPONENTS + "stat_mark.html", takes_context=True)
def stat_mark(context: Context, metric: str) -> dict[str, object]:
    """{% stat_mark "survival" %} after a ratio on the profile: a "Top 10%" / "Top 25%" badge when the pilot's value
    (from `stats`) is above the p90 / p75 of the pilots with enough sorties (`marks`, from the view; FR-WEB-22).
    Nothing for low values, a pilot under the minimum, an undefined ratio or a scope without thresholds."""
    stats = context.get("stats")
    marks = cast(Mapping[str, StatThreshold], context.get("marks") or {})
    limits = marks.get(metric)
    if stats is None or limits is None:
        return {}
    kind = cast(stat_marks.Metric, metric)
    totals = stat_marks_totals(stats)
    if kind in stat_marks.ELO_METRICS:  # ratings are all time and live on the player, also on a tour profile
        player = context.get("player")
        if player is None:
            return {}
        totals = dataclasses.replace(
            totals,
            elo_prop=player.elo_prop,
            elo_prop_games=player.elo_prop_games,
            elo_jet=player.elo_jet,
            elo_jet_games=player.elo_jet_games,
        )
    if stat_marks.amount(kind, totals) < limits.min_sorties:
        return {}
    found = stat_marks.band(
        stat_marks.metric_value(kind, totals),
        stat_marks.Thresholds(limits.p10, limits.p25, limits.p50, limits.p75, limits.p90, limits.population),
    )
    return {
        "band": found,
        "min_sorties": limits.min_sorties,
        "unit": stat_marks.unit(kind),
        "minutes": max(
            1, math.ceil(limits.min_sorties / 60)
        ),  # for the time on target / flight time (stored in seconds)
    }


@register.inclusion_tag(COMPONENTS + "stat_mark_note.html", takes_context=True)
def stat_mark_note(context: Context) -> dict[str, object]:
    """{% stat_mark_note %} under the ratios: why a pilot with too few sorties has no marks (FR-WEB-22)."""
    stats = context.get("stats")
    marks = cast(Mapping[str, StatThreshold], context.get("marks") or {})
    # The sortie-based marks only: Elo and time-on-target rows carry other minimums
    first = next((m for key, m in marks.items() if stat_marks.unit(cast(stat_marks.Metric, key)) == "sorties"), None)
    if stats is None or first is None or stats.sorties >= first.min_sorties:
        return {}
    return {"min_sorties": first.min_sorties}


@register.simple_tag
def flavor(spot: str, seed: object) -> str:
    """`{% flavor "spot" seed %}`: a stable, translated one-liner for a highlight spot (il2ks.web.flavor)."""
    return str(flavor_text.pick(spot, seed))


@register.simple_tag(takes_context=True)
def shame_flavor(context: Context, stats: Counters, seed: object) -> str:
    """`{% shame_flavor stats player.pk %}`: the hall-of-shame quip, by which incidents the pilot has and whether
    their rate is in the top 10% (`marks` from the view; il2ks.web.flavor.shame_spot)."""
    marks = cast(Mapping[str, StatThreshold], context.get("marks") or {})
    return flavor(flavor_text.shame_spot(stats, marks), seed)


@register.simple_tag
def sortie_flavor(sortie: PlayerSortie, detail: object = None) -> str:
    """`{% sortie_flavor sortie detail as quip %}`: the sortie's line, or '' for an ordinary sortie. `detail` is the
    page's `sortie_view.Detail`; its `highlights` unlock the spots that need the timeline."""
    highlights = getattr(detail, "highlights", None)
    spot = flavor_text.sortie_spot(sortie, highlights if isinstance(highlights, flavor_text.Highlights) else None)
    return "" if spot is None else flavor(spot, sortie.pk)


@register.inclusion_tag(COMPONENTS + "empty_row.html")
def empty_row(colspan: int, message: object = "") -> dict[str, object]:
    """{% empty_row 6 %} inside <tbody> when there are no rows."""
    return {"colspan": colspan, "message": message}


@register.inclusion_tag(COMPONENTS + "breadcrumbs.html")
def breadcrumbs(crumbs: Iterable[Crumb]) -> dict[str, object]:
    """{% breadcrumbs crumbs %} with (label, url) pairs; the last one (url None) is the current page."""
    return {"crumbs": list(crumbs)}


@register.inclusion_tag(COMPONENTS + "dropdown.html")
def dropdown(label: object, items: Iterable[tuple[object, str]], align: str = "") -> dict[str, object]:
    """{% dropdown _("Links") links %} with (label, href) pairs: a Pico `<details class="dropdown">` menu."""
    return {"label": label, "items": list(items), "align": align}


LANGUAGE_NAMES: dict[str, str] = {"pt-br": "Português"}
"""Menu names that differ from Django's `name_local` ("Português brasileiro"): the Brazil flag says the rest."""

LANGUAGE_FLAGS: dict[str, str] = {
    "en": "flag/us",  # the site's English is American English
    "ru": "flag/ru",
    "de": "flag/de",
    "es": "flag/es",
    "fr": "flag/fr",
    "pt-br": "flag/br",
}
"""Language code -> flag file (no extension) under static/il2ks/img/; decorative, the language name is the label."""


@register.inclusion_tag(COMPONENTS + "language_menu.html", takes_context=True)
def language_menu(context: Context) -> dict[str, object]:
    """{% language_menu %}: the language switcher, a button-like dropdown of flag + name (footer).

    The summary shows the current language in its own language; the items link to `web:set-language`, which stores the
    choice in a cookie and returns to the current page (`web.views.language`). Works without JS (a `<details>`)."""
    request = _request_of(context)
    back = request.get_full_path() if request is not None else "/"
    current = get_language() or settings.LANGUAGE_CODE
    items: list[dict[str, object]] = []
    for code, _name in settings.LANGUAGES:
        flag = LANGUAGE_FLAGS.get(code)
        items.append(
            {
                "code": code,
                "name": LANGUAGE_NAMES.get(code) or str(get_language_info(code)["name_local"]).capitalize(),
                "flag": static(f"il2ks/img/{flag}.svg") if flag else "",
                "href": f"{reverse('web:set-language')}?{urlencode({'language': code, 'next': back})}",
                "current": code == current,
            }
        )
    active = next((item for item in items if item["current"]), items[0])
    return {"current": active, "items": items}


# --- table, sorting, paging, filters ------------------------------------------------------------------------------
@register.inclusion_tag(COMPONENTS + "sort_th.html", takes_context=True)
def sort_th(
    context: Context,
    field: str,
    label: object,
    current: str | None = None,
    numeric: bool = False,
    first: SortFirst | None = None,
) -> dict[str, object]:
    """{% sort_th "kills" _("Kills") numeric=True %}: a sortable <th>.

    `current` defaults to the context variable `sort` (the view's whitelisted, resolved value: 'kills' or '-kills').
    The first click sorts descending for numeric columns and ascending otherwise (override with first="asc"/"desc")."""
    active = current if current is not None else str(context.get("sort") or "")
    direction = "desc" if active == f"-{field}" else "asc" if active == field else ""
    target = display.next_sort(active, field, first or ("desc" if numeric else "asc"))
    return {
        "label": label,
        "numeric": numeric,
        "direction": direction,
        "aria_sort": {"asc": "ascending", "desc": "descending"}.get(direction, "none"),
        "href": replace_query(_params_of(context), {"sort": target, **_page_resets(_params_of(context))}),
    }


@register.inclusion_tag(COMPONENTS + "pagination.html", takes_context=True)
def pagination(context: Context, page_obj: Page, param: str = "page") -> dict[str, object]:
    """{% pagination page_obj %} for a Django `Page`: result summary plus numbered links that keep other parameters,
    every value of a repeated one (`cols`) included.

    A page with several paginated tables gives each its own query parameter, named 'page_...':
    {% pagination group.page param=group.page_param %}. Sorting drops them all (a new order starts at page 1)."""
    params = _params_of(context)
    paginator = page_obj.paginator
    links = [
        {
            "number": link.number,
            "current": link.current,
            "href": replace_query(params, {param: link.number if link.number != 1 else None})
            if link.number is not None
            else "",
        }
        for link in display.page_links(page_obj.number, paginator.num_pages)
    ]
    return {
        "page_obj": page_obj,
        "total": paginator.count,
        "first_index": page_obj.start_index(),
        "last_index": page_obj.end_index(),
        "multiple": paginator.num_pages > 1,
        "links": links,
        "prev_href": (
            replace_query(params, {param: page_obj.previous_page_number() if page_obj.number > 2 else None})
            if page_obj.has_previous()
            else ""
        ),
        "next_href": replace_query(params, {param: page_obj.next_page_number()}) if page_obj.has_next() else "",
    }


@register.inclusion_tag(COMPONENTS + "filter_select.html", takes_context=True)
def filter_select(
    context: Context,
    name: str,
    label: object,
    options: Iterable[Option],
    selected: object = None,
    all_label: object = "",
) -> dict[str, object]:
    """{% filter_select "aircraft" _("Aircraft") options all_label=_("All aircraft") %} inside {% filter_bar %}.

    `options` are (value, label) pairs. The selected value is read from `?name=` unless `selected` is given."""
    chosen = str(selected) if selected is not None else _params_of(context).get(name, "")
    rows = [(str(value), label_, str(value) == chosen) for value, label_ in options]
    return {"name": name, "label": label, "options": rows, "all_label": all_label, "chosen": chosen}


@register.inclusion_tag(COMPONENTS + "filter_text.html", takes_context=True)
def filter_text(
    context: Context, name: str, label: object, placeholder: object = "", live: bool = False
) -> dict[str, object]:
    """{% filter_text "q" _("Name") live=True %} inside {% filter_bar %}; live=True also submits while typing."""
    return {
        "name": name,
        "label": label,
        "placeholder": placeholder,
        "live": live,
        "value": _params_of(context).get(name, ""),
    }


@register.inclusion_tag(COMPONENTS + "bar_chart.html")
def bar_chart(spec: ChartSpec) -> dict[str, object]:
    """{% bar_chart spec %}: an inline-SVG bar chart (FR-WEB-16) with legend (two or more series), tooltips and a table
    of the numbers. `spec` is a `il2ks.web.charts.ChartSpec`, usually built in `il2ks.web.chart_data`."""
    return {"spec": spec, "chart": build_bar_chart(spec)}


def _tour_context(context: Context, tours: Iterable[Tour], selected: Tour | None, *, main: bool) -> dict[str, object]:
    """What the tour components render: the select, the current-tour / all-time toggle and the next tour's start.

    `selected` None means all time. The toggle links keep the other query parameters (a sort, a filter) and drop
    `page`. `next_start` is the end of the newest tour when the calendar fixed it (never in manual mode), shown for
    every viewer alike; `localtime.js` hides it once that moment has passed, so the markup stays a function of the data
    (TD-28). `main` is whether the htmx swap target is `<main>` (the profile) rather than a `results_region`."""
    rows = list(tours)
    params = _params_of(context)
    current = tour_reads.current_tour_of(rows)
    return {
        "tours": rows,
        "selected_id": selected.pk if selected is not None else tour_reads.TOUR_ALL,
        "all_value": tour_reads.TOUR_ALL,
        "current": current,
        "is_all": selected is None,
        "is_current": selected is not None and selected == current,
        "all_href": replace_query(params, {tour_reads.TOUR_PARAM: tour_reads.TOUR_ALL, "page": None}),
        "current_href": replace_query(
            params, {tour_reads.TOUR_PARAM: current.pk if current is not None else None, "page": None}
        ),
        "next_start": current.ended_at if current is not None else None,
        "main": main,
    }


@register.inclusion_tag(COMPONENTS + "tour_select.html", takes_context=True)
def tour_select(context: Context, tours: Iterable[Tour], selected: Tour | None = None) -> dict[str, object]:
    """{% tour_select tours tour %}: the tour selector and all-time toggle as a form of its own (`?tour=<id>` or
    `?tour=all`, no parameter = the current tour), TD-26.

    `tours` and `selected` are the fields of `il2ks.queries.tours.tour_choice_from(request.GET)`."""
    request = _request_of(context)
    hidden = [
        (key, value) for key, values in _params_of(context).lists() if key not in {"tour", "page"} for value in values
    ]
    return {
        **_tour_context(context, tours, selected, main=True),
        "action": request.path if request is not None else "",
        "hidden": hidden,
    }


@register.inclusion_tag(COMPONENTS + "tour_filter.html", takes_context=True)
def tour_filter(context: Context, tours: Iterable[Tour], selected: Tour | None = None) -> dict[str, object]:
    """{% tour_filter tours tour %} inside {% filter_bar %}: the same selector and toggle for a list page."""
    return _tour_context(context, tours, selected, main=False)


@register.inclusion_tag(COMPONENTS + "columns_picker.html", takes_context=True)
def columns_picker(context: Context, available: Sequence[columns.Column[Never]]) -> dict[str, object]:
    """{% columns_picker optional_columns %} inside {% filter_bar %}: the "Columns" dropdown of checkboxes (`?cols=`).

    `available` is the page's registry of optional columns (`il2ks.web.columns`); the checked ones are read from the
    query string, so the control and the table can never disagree."""
    wanted = columns.requested_keys(_params_of(context))
    return {"options": [(column.key, column.label, column.hint, column.key in wanted) for column in available]}


@register.filter
def cell(column: object, row: object) -> str:
    """{{ column|cell:row }}: the text of an optional column (`il2ks.web.columns.Column`) for one row."""
    return cast("columns.Column[object]", column).cell(row)


# --- block tags ---------------------------------------------------------------------------------------------------
class ComponentBlockNode(Node):
    """Renders `template_name` with the tag's arguments plus `content` (the rendered body) and `request`."""

    def __init__(
        self,
        template_name: str,
        nodelist: NodeList,
        values: Mapping[str, FilterExpression],
        extra: Callable[[Context], Mapping[str, object]] | None,
    ) -> None:
        self.template_name = template_name
        self.nodelist = nodelist
        self.values = values
        self.extra = extra

    def render(self, context: Context) -> SafeString:
        resolved = {name: expr.resolve(context) for name, expr in self.values.items()}  # pyright: ignore[reportArgumentType]
        data: dict[str, object] = dict(resolved)
        data["content"] = self.nodelist.render(context)
        data["request"] = _request_of(context)
        if self.extra is not None:
            data.update(self.extra(context))
        return SafeString(render_to_string(self.template_name, data))


def _register_block(
    name: str,
    template_name: str,
    params: Sequence[str],
    extra: Callable[[Context], Mapping[str, object]] | None = None,
) -> None:
    """Registers `{% name arg ... key=value %}...{% endname %}`; `params` names the positional arguments in order."""

    def compile_tag(parser: Parser, token: Token) -> Node:
        bits = token.split_contents()[1:]
        values: dict[str, FilterExpression] = {}
        position = 0
        for bit in bits:
            match = kwarg_re.match(bit)
            key = match.group(1) if match is not None else None
            if key is None:
                if position >= len(params):
                    raise TemplateSyntaxError(f"{name!r} takes at most {len(params)} positional arguments")
                values[params[position]] = parser.compile_filter(bit)
                position += 1
            else:
                values[key] = parser.compile_filter(bit.split("=", 1)[1])
        nodelist = parser.parse((f"end{name}",))
        parser.delete_first_token()
        return ComponentBlockNode(template_name, nodelist, values, extra)

    register.tag(name, compile_tag)


def _kept_params(params: QueryDict) -> QueryDict:
    """What "Clear filters" keeps besides the sort: the chosen columns."""
    kept = QueryDict(mutable=True)
    kept.setlist(columns.COLS_PARAM, params.getlist(columns.COLS_PARAM))
    return kept


def _filter_bar_extra(context: Context) -> dict[str, object]:
    request = _request_of(context)
    params = _params_of(context)
    active = any(value for key, values in params.lists() for value in values if key not in NON_FILTER_PARAMS)
    return {
        "action": request.path if request is not None else "",
        "sort": params.get("sort", ""),
        "has_filters": active,
        "clear_href": replace_query(_kept_params(params), {"sort": params.get("sort", "")}),
    }


_register_block("results_region", COMPONENTS + "results_region.html", ())
_register_block("filter_bar", COMPONENTS + "filter_bar.html", (), _filter_bar_extra)
_register_block("accordion", COMPONENTS + "accordion.html", ("title", "hint"))
_register_block("notice", COMPONENTS + "notice.html", ("kind", "title"))


@register.filter
def utc_date(value: datetime | None) -> SafeString | str:
    """Deprecated alias of `local_date` (the UTC filter was removed with FR-WEB-17); keeps old `custom/` overrides
    rendering (TD-25)."""
    return local_date(value)
