"""A player's sortie list, the site-wide sortie list and the sortie detail page (FR-WEB-5, FR-WEB-6, FR-WEB-13,
FR-WEB-29).

Both are simple reads (TD-22): the list is one paginated query plus the filter choices; the detail page reads the sortie
with its joins, the kill rows, the counterpart sorties and the game objects the stored JSON refers to (5 queries).
Hidden players and missions answer 404 (FR-ADM-3).
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace

from django.contrib.staticfiles import finders
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils.translation import get_language
from django.utils.translation import gettext as _

from il2ks.config import RuleSet
from il2ks.db.models import CombatRole, Outcome, Player, PlayerSortie, Role
from il2ks.queries import leaderboards as aircraft_reads
from il2ks.queries import sorties as reads
from il2ks.queries.paging import ROW_PAGE_SIZE
from il2ks.queries.stat_marks import sortie_thresholds
from il2ks.queries.tours import is_quiet_tour, pilot_absence, tour_choice_from, tour_id_query, tour_query
from il2ks.rule_settings import effective_rules
from il2ks.web import columns, display, object_names
from il2ks.web.admin_rules import base_rules
from il2ks.web.context_processors import site_row
from il2ks.web.sortie_view import (
    Lookup,
    build_detail,
    counterpart_object_types,
    counterpart_sortie_ids,
    sortie_marks,
)

DAMAGE_PARAM = "page_damage"
OG_IMAGE = "il2ks/img/brand/og-default.png"


def _options(values: Sequence[str], labels: Mapping[str, display.BadgeSpec]) -> list[tuple[str, str]]:
    """(value, translated label) pairs for a filter, labelled like the badges that show the same values."""
    return [(value, display.badge_spec(labels, value)[0]) for value in values]


def player_sorties(request: HttpRequest, pk: int) -> HttpResponse:
    """`/players/<pk>/sorties/`: newest first, sortable, filterable by aircraft, outcome, role and combat role.

    Query parameters: `tour` (Tour pk, TD-26; unknown = all time), `aircraft` (GameObject pk of one of the player's
    aircraft), `outcome`, `role`, `combat_role`,
    `sort` (one of `reads.SORT_FIELDS`, '-' prefix = descending), `page`. Unknown values are ignored.

    Context: player, tours, tour (None = all time), quiet_tour, page_obj (PlayerSortie rows with mission
    and aircraft), sort (resolved),
    aircraft_options,
    outcome_options, role_options, combat_role_options ((value, label) pairs), crumbs, page_title, optional_columns
    (every column a visitor can add) and columns (the ones `?cols=` chose; all sortable)."""
    player = get_object_or_404(Player.objects.visible(), pk=pk)
    aircraft = reads.player_aircraft(player)
    language = get_language() or "en"
    choice = tour_choice_from(request.GET)
    filters = replace(
        reads.parse_filters(
            {name: request.GET.get(name, "") for name in ("aircraft", "outcome", "role", "combat_role")},
            {row.aircraft_id for row in aircraft},
        ),
        tour=choice.selected,
    )
    sort = reads.resolve_sort(request.GET.get("sort", ""))
    shown = columns.chosen(request.GET, columns.SORTIE_COLUMNS)
    page = reads.sortie_page(player, filters, sort, request.GET.get("page", "1"))
    crumbs = [
        (_("Players"), reverse("web:player-search")),
        (player.current_name, reverse("web:player-detail", args=[player.pk]) + tour_query(choice.selected)),
        (_("Sorties"), None),
    ]
    quiet_tour = is_quiet_tour(choice.selected, request.GET, page.paginator.count)
    return render(
        request,
        "il2ks/sorties/list.html",
        {
            "player": player,
            **choice.context,
            "page_obj": page,
            "quiet_tour": quiet_tour,
            "absence": pilot_absence(player.pk, choice) if quiet_tour else None,  # only an empty page pays the query
            "colspan": 11 + len(shown),
            "sort": sort,
            "optional_columns": columns.SORTIE_COLUMNS,
            "columns": shown,
            "aircraft_options": [(row.aircraft_id, object_names.name_of(row.aircraft, language)) for row in aircraft],
            "outcome_options": _options(Outcome.values, display.OUTCOMES),
            "role_options": [(Role.PILOT.value, _("Pilot")), (Role.GUNNER.value, _("Gunner"))],
            "combat_role_options": _options(CombatRole.values, display.ROLES),
            "crumbs": crumbs,
            "page_title": _("%(name)s: sorties") % {"name": player.current_name},
        },
    )


def all_sorties(request: HttpRequest) -> HttpResponse:
    """`/sorties/`: every counted sortie of the site, newest first (maintainer request 2026-10-05, FR-WEB-29).

    Query parameters: `tour` (TD-26: none = the current tour, `all` = all time), `q` (pilot name contains), `aircraft`
    (GameObject pk of a playable aircraft), `combat_role`, `outcome`, `seat` (none = pilot sorties, like the statistics;
    `gunner`; `any`), `sort` (one of `reads.SORT_FIELDS`, '-' prefix = descending), `cols`
    (`columns.SITE_SORTIE_COLUMNS`),
    `page`. Unknown values are ignored. Hidden players and hidden missions are left out (their sortie page is a 404).

    Context: tours, tour (None = all time), quiet_tour, page_obj (PlayerSortie rows with player, mission and
    aircraft), sort, seat (the parsed seat value for the filter), aircraft_options, outcome_options, seat_options,
    combat_role_options, optional_columns, columns, colspan, page_title.
    Reads: site context (2), tours, aircraft choices, count, page."""
    planes = aircraft_reads.aircraft_options()
    language = get_language() or "en"
    choice = tour_choice_from(request.GET)
    filters = replace(
        reads.parse_filters(
            {name: request.GET.get(name, "") for name in ("aircraft", "outcome", "combat_role")}, {p.pk for p in planes}
        ),
        tour=choice.selected,
        role=reads.parse_seat(request.GET.get("seat", "")),
        pilot=reads.parse_pilot(request.GET.get("q", "")),
    )
    sort = reads.resolve_sort(request.GET.get("sort", ""))
    shown = columns.chosen(request.GET, columns.SITE_SORTIE_COLUMNS)
    page = reads.all_sortie_page(filters, sort, request.GET.get("page", "1"))
    return render(
        request,
        "il2ks/sorties/all.html",
        {
            **choice.context,
            "page_obj": page,
            "quiet_tour": is_quiet_tour(choice.selected, request.GET, page.paginator.count),
            "colspan": 9 + len(shown),
            "sort": sort,
            "optional_columns": columns.SITE_SORTIE_COLUMNS,
            "columns": shown,
            "aircraft_options": [(a.pk, object_names.name_of(a, language)) for a in planes],
            "outcome_options": _options(Outcome.values, display.OUTCOMES),
            "seat_options": [(Role.GUNNER.value, _("Gunner")), (reads.SEAT_ANY, _("Pilot or gunner"))],
            "combat_role_options": _options(CombatRole.values, display.ROLES),
            "page_title": _("Sorties"),
        },
    )


def _og_description(sortie: PlayerSortie) -> str:
    """The Discord preview text: kills, flight time, mission date."""
    parts = [
        _("%(n)s air kills") % {"n": sortie.kills_air},
        _("%(n)s ground kills") % {"n": sortie.kills_ground},
        _("%(n)s assists") % {"n": sortie.assists},
        _("flight time %(t)s") % {"t": display.duration(sortie.flight_time_s)},
        display.utc(sortie.spawned_at),
    ]
    return ", ".join(parts)


def _plain(value: float) -> str:
    """A rule number without trailing zeros, in the viewer's number format (0.5 -> "0.5" / "0,5", 15.0 -> "15")."""
    return display.num(value, 0 if float(value).is_integer() else min(3, len(f"{value:.3f}".rstrip("0").split(".")[1])))


def ram_note(request: HttpRequest) -> str:
    """The line under the timeline of a sortie with a ram, with the thresholds in force (the admin's applied values
    over the file's; the site row is already read for the page, no extra query)."""
    rules: RuleSet = effective_rules(base_rules(), site_row(request).rule_settings_applied)
    toggles = rules.replay.toggles
    return _(
        # Translators: note under the sortie timeline when the sortie had a mid-air collision (a "ram"); the two numbers
        # are the admin-set limits: seconds (can be a fraction like 0.5) and meters between the two aircraft's losses
        "Rams are detected from the log (two aircraft lost within %(seconds)s s and %(meters)s m of each other) "
        "and may occasionally be wrong."
    ) % {"seconds": _plain(toggles.ram_window_s), "meters": _plain(toggles.ram_distance_m)}


def sortie_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """`/sorties/<pk>/`: the core page. Context: sortie (with player, mission, aircraft), detail
    (`il2ks.web.sortie_view.Detail`), damage_page (20 rows a page, `?page_damage=`; OQ-96; the timeline is
    not paginated: maintainer, 2026-10-04), og (title, description, url, image) for the `head` block, page_title.

    `sortie_marks`: Top 1% / 5% / 10% / 25% badges of the sortie's air and ground kills (FR-WEB-22), read only for a
    pilot sortie with kills (one more query, the stored sortie populations of its tour and of all time).
    `damage_open`: the damage section is collapsed, unless a damage page was asked for.

    Reads: sortie + joins (1), kills made (1), kills suffered (1), counterpart sorties (1), game objects (1),
    sortie populations (1, only when the sortie has kills)."""
    sortie = reads.visible_sortie(pk)
    if sortie is None:
        raise Http404
    made = reads.kills_made(sortie)
    suffered = reads.kills_suffered(sortie)
    lookup = Lookup(
        reads.sorties_by_id(counterpart_sortie_ids(sortie, [*made, *suffered])),
        reads.objects_by_log_name(counterpart_object_types(sortie)),
    )
    detail = build_detail(sortie, made, suffered, lookup)
    marks = (
        sortie_marks(sortie, sortie_thresholds(sortie.mission.tour_id))
        if sortie.role == Role.PILOT and (sortie.kills_air or sortie.kills_ground)
        else {}
    )
    damage_page = Paginator(detail.damage, ROW_PAGE_SIZE).get_page(request.GET.get(DAMAGE_PARAM))
    outcome = display.badge_spec(display.OUTCOMES, sortie.outcome)[0]
    aircraft = object_names.name_of(sortie.aircraft, get_language() or "en")
    title = f"{sortie.name_at_time} — {aircraft} — {outcome}"
    image = static(OG_IMAGE) if finders.find(OG_IMAGE) else ""
    return render(
        request,
        "il2ks/sorties/detail.html",
        {
            "sortie": sortie,
            "detail": detail,
            "damage_page": damage_page,
            "damage_param": DAMAGE_PARAM,
            "damage_open": DAMAGE_PARAM in request.GET,
            "sortie_marks": marks,
            "ram_note": ram_note(request) if detail.has_ram else "",
            "crumbs": [
                (_("Players"), reverse("web:player-search")),
                (
                    sortie.player.current_name,
                    reverse("web:player-detail", args=[sortie.player_id]) + tour_id_query(sortie.mission.tour_id),
                ),
                (_("Sorties"), reverse("web:player-sorties", args=[sortie.player_id]) + "?tour=all"),
                (_("Sortie report"), None),
            ],
            "page_title": title,
            "og": {
                "title": title,
                "description": _og_description(sortie),
                "url": request.build_absolute_uri(),
                "image": request.build_absolute_uri(image) if image else "",
            },
        },
    )
