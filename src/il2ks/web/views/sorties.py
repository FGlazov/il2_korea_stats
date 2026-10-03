"""A player's sortie list and the sortie detail page (FR-WEB-5, FR-WEB-6, FR-WEB-13).

Both are simple reads (TD-22): the list is one paginated query plus the filter choices; the detail page reads the sortie
with its joins, the kill rows, the counterpart sorties and the game objects the stored JSON refers to (5 queries).
Hidden players and missions answer 404 (FR-ADM-3).
"""

from collections.abc import Mapping, Sequence

from django.contrib.staticfiles import finders
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils.translation import get_language
from django.utils.translation import gettext as _

from il2ks.db.models import CombatRole, Outcome, Player, PlayerSortie, Role
from il2ks.queries import sorties as reads
from il2ks.web import display, object_names
from il2ks.web.sortie_view import Lookup, build_detail, counterpart_object_types, counterpart_sortie_ids

OG_IMAGE = "il2ks/img/brand/og-default.png"


def _options(values: Sequence[str], labels: Mapping[str, display.BadgeSpec]) -> list[tuple[str, str]]:
    """(value, translated label) pairs for a filter, labelled like the badges that show the same values."""
    return [(value, display.badge_spec(labels, value)[0]) for value in values]


def player_sorties(request: HttpRequest, pk: int) -> HttpResponse:
    """`/players/<pk>/sorties/`: newest first, sortable, filterable by aircraft, outcome, role and combat role.

    Query parameters: `aircraft` (GameObject pk of one of the player's aircraft), `outcome`, `role`, `combat_role`,
    `sort` (one of `reads.SORT_FIELDS`, '-' prefix = descending), `page`. Unknown values are ignored.

    Context: player, page_obj (PlayerSortie rows with mission and aircraft), sort (resolved), aircraft_options,
    outcome_options, role_options, combat_role_options ((value, label) pairs), crumbs, page_title."""
    player = get_object_or_404(Player.objects.visible(), pk=pk)
    aircraft = reads.player_aircraft(player)
    language = get_language() or "en"
    filters = reads.parse_filters(
        {name: request.GET.get(name, "") for name in ("aircraft", "outcome", "role", "combat_role")},
        {row.aircraft_id for row in aircraft},
    )
    sort = reads.resolve_sort(request.GET.get("sort", ""))
    page = reads.sortie_page(player, filters, sort, request.GET.get("page", "1"))
    crumbs = [
        (_("Players"), reverse("web:player-search")),
        (player.current_name, reverse("web:player-detail", args=[player.pk])),
        (_("Sorties"), None),
    ]
    return render(
        request,
        "il2ks/sorties/list.html",
        {
            "player": player,
            "page_obj": page,
            "sort": sort,
            "aircraft_options": [(row.aircraft_id, object_names.name_of(row.aircraft, language)) for row in aircraft],
            "outcome_options": _options(Outcome.values, display.OUTCOMES),
            "role_options": [(Role.PILOT.value, _("Pilot")), (Role.GUNNER.value, _("Gunner"))],
            "combat_role_options": _options(CombatRole.values, display.ROLES),
            "crumbs": crumbs,
            "page_title": _("%(name)s: sorties") % {"name": player.current_name},
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


def sortie_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """`/sorties/<pk>/`: the core page. Context: sortie (with player, mission, aircraft), detail
    (`il2ks.web.sortie_view.Detail`), og (title, description, url, image) for the `head` block, page_title.

    Reads: sortie + joins (1), kills made (1), kills suffered (1), counterpart sorties (1), game objects (1)."""
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
            "crumbs": [
                (_("Players"), reverse("web:player-search")),
                (sortie.player.current_name, reverse("web:player-detail", args=[sortie.player_id])),
                (_("Sorties"), reverse("web:player-sorties", args=[sortie.player_id])),
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
