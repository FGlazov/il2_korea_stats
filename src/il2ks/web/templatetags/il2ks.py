"""Template filters and tags of the web UI: `{% load il2ks %}`.

The tags render the files in `templates/il2ks/components/`; every component documents its context at the top of
its template, and server owners may override any of them (TD-25). Tags that need the current URL read the request from
the context (`django.template.context_processors.request`, enabled in settings) and keep the other query parameters.

Filters (formatting only, TD-22): duration, utc, local_time, local_short, local_date, local_hm, local_clock, num,
ratio, per_hour, percent, mission_title, game_when,
clock_since; object_name (TD-24: show a GameObject in the viewer's language, never `.display_name` directly).
Tags: icon, aircraft_icon, side, badge, coalition_badge, coalition_icon, winner_badge, outcome_badge, fate_badge,
status_badge, aircraft_badge, role_badge, stat_tile, kv_list, empty_row, breadcrumbs, dropdown, language_menu, sort_th,
pagination, filter_select, filter_text, tour_select.
Block tags: results_region, filter_bar, accordion, notice.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime

from django import template
from django.conf import settings
from django.core.paginator import Page
from django.http import HttpRequest, QueryDict
from django.template import Context, Node, NodeList, TemplateSyntaxError
from django.template.base import FilterExpression, Parser, Token, kwarg_re
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.safestring import SafeString
from django.utils.translation import get_language, get_language_info

from il2ks.db.models import Tour
from il2ks.web import display, icons, object_names
from il2ks.web.display import SortFirst, Tone

register = template.Library()

COMPONENTS = "il2ks/components/"
NON_FILTER_PARAMS = frozenset({"page", "sort"})

type Option = tuple[object, object]
type Crumb = tuple[object, str | None]


# --- query string helpers -----------------------------------------------------------------------------------------
def _request_of(context: Context) -> HttpRequest | None:
    request = context.get("request")
    return request if isinstance(request, HttpRequest) else None


def _params_of(context: Context) -> QueryDict:
    request = _request_of(context)
    return request.GET if request is not None else QueryDict()


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


@register.simple_tag(takes_context=True)
def language_menu(context: Context) -> dict[str, object]:
    """{% language_menu as languages %} then {% dropdown languages.label languages.items %}: the language switcher.

    The label is the current language in its own language; the items link to `web:set-language`, which stores the
    choice in a cookie and returns to the current page (`web.views.language`)."""
    request = _request_of(context)
    back = request.get_full_path() if request is not None else "/"
    current = get_language() or settings.LANGUAGE_CODE
    items: list[tuple[str, str]] = []
    label = ""
    for code, _name in settings.LANGUAGES:
        own_name = str(get_language_info(code)["name_local"]).capitalize()
        items.append((own_name, f"{reverse('web:set-language')}?{urlencode({'language': code, 'next': back})}"))
        if code == current:
            label = own_name
    return {"label": label or items[0][0], "items": items}


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
        "href": replace_query(_params_of(context), {"sort": target, "page": None}),
    }


@register.inclusion_tag(COMPONENTS + "pagination.html", takes_context=True)
def pagination(context: Context, page_obj: Page) -> dict[str, object]:
    """{% pagination page_obj %} for a Django `Page`: result summary plus numbered links that keep other parameters."""
    params = _params_of(context)
    paginator = page_obj.paginator
    links = [
        {
            "number": link.number,
            "current": link.current,
            "href": replace_query(params, {"page": link.number if link.number != 1 else None})
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
            replace_query(params, {"page": page_obj.previous_page_number() if page_obj.number > 2 else None})
            if page_obj.has_previous()
            else ""
        ),
        "next_href": replace_query(params, {"page": page_obj.next_page_number()}) if page_obj.has_next() else "",
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


@register.inclusion_tag(COMPONENTS + "tour_select.html", takes_context=True)
def tour_select(context: Context, tours: Iterable[Tour], selected: Tour | None = None) -> dict[str, object]:
    """{% tour_select tours tour %}: the tour selector (`?tour=<id>`, "All time" = no parameter), TD-26.

    `tours` and `selected` are the fields of `il2ks.queries.tours.tour_choice(request.GET.get("tour"))`."""
    request = _request_of(context)
    params = _params_of(context)
    hidden = [(key, value) for key, values in params.lists() if key not in {"tour", "page"} for value in values]
    return {
        "tours": list(tours),
        "selected_id": selected.pk if selected is not None else "",
        "action": request.path if request is not None else "",
        "hidden": hidden,
    }


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


def _filter_bar_extra(context: Context) -> dict[str, object]:
    request = _request_of(context)
    params = _params_of(context)
    active = any(value for key, values in params.lists() for value in values if key not in NON_FILTER_PARAMS)
    return {
        "action": request.path if request is not None else "",
        "sort": params.get("sort", ""),
        "has_filters": active,
        "clear_href": replace_query(QueryDict(), {"sort": params.get("sort", "")}),
    }


_register_block("results_region", COMPONENTS + "results_region.html", ())
_register_block("filter_bar", COMPONENTS + "filter_bar.html", (), _filter_bar_extra)
_register_block("accordion", COMPONENTS + "accordion.html", ("title", "hint"))
_register_block("notice", COMPONENTS + "notice.html", ("kind", "title"))
