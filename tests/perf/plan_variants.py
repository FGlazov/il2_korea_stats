"""The pages of `tests.perf.pages.PAGES` and their filter and sort variants, for the query-plan test.

Every `?sort=` key of every sortable list is a variant (ascending and descending), so a sort that needs a temp b-tree is
seen. Built from the same whitelists the views use: a new sort key is picked up automatically."""

from collections.abc import Callable
from dataclasses import dataclass

from tests.perf.pages import PAGES
from tests.perf.seed import SeededWorld

from il2ks.db.models import AircraftRole, GameObject, Outcome
from il2ks.queries import aircraft as aircraft_reads
from il2ks.queries import boards as board_reads
from il2ks.queries import leaderboards as leaderboard_reads
from il2ks.queries import missions as mission_reads
from il2ks.queries import players as player_reads
from il2ks.queries import sorties as sortie_reads


@dataclass(frozen=True)
class Variant:
    url_name: str
    url: Callable[[SeededWorld], str]
    label: str
    rare_sort: bool = False
    """The visitor picks a sort column that is not one of the indexed ones (`STRICT_SORTS`): the statement may read and
    order the whole scoped table (`query_plans.problems`)."""


def _fixed(url_name: str, url: str, *, rare_sort: bool = False) -> Variant:
    return Variant(url_name, lambda w: url, url, rare_sort)


def _built(url_name: str, build: Callable[[SeededWorld], str], label: str, *, rare_sort: bool = False) -> Variant:
    return Variant(url_name, build, label, rare_sort)


def _sorts(
    name: str, base: Callable[[SeededWorld], str], keys: list[str], label: str, indexed: tuple[str, ...] = ()
) -> list[Variant]:
    """`base?sort=key` and `base?sort=-key` for every key (`base` already ends in `?` or `&`). The keys in `indexed` are
    the default and the common sorts of the list, with their direction (`-score`): they get an index and are checked
    strictly; every other key or direction is a rare sort (a visitor clicking a column header), allowed to sort the
    scoped table."""
    return [
        Variant(
            name,
            lambda w, k=key, p=prefix: f"{base(w)}sort={p}{k}",
            f"{label} sort={prefix}{key}",
            prefix + key not in indexed,
        )
        for key in keys
        for prefix in ("", "-")
    ]


PLAYER_LIST_INDEXED = ("-last_seen", "name", "-kills_air", "-flight_time_s")
"""The player list's default sort (last seen) and the columns visitors sort by most: indexed on `Player`."""


def _variants() -> list[Variant]:
    found: list[Variant] = [
        Variant(spec.url_name, spec.url, spec.url_name + " " + spec.url(_PLACEHOLDER)) for spec in PAGES
    ]

    for board in leaderboard_reads.BOARDS.values():
        url = f"/leaderboards/{board.key}/"
        found.append(_fixed("leaderboard", url + "?tour=all"))
        default = (board.default_sort,)
        found += _sorts("leaderboard", lambda w, u=url: u + "?tour=all&", list(board.sorts), url, default)
        if board.per_tour:
            found += _sorts(
                "leaderboard", lambda w, u=url: u + "?", list(board.sorts), url + " (current tour)", default
            )
        for pool in leaderboard_reads.POOLS if board.per_pool else ():
            found.append(_fixed("leaderboard", f"{url}?pool={pool}"))
            found.append(_fixed("leaderboard", f"{url}?pool={pool}&tour=all"))
        if board.per_aircraft:
            found.append(_built("leaderboard", lambda w, u=url: f"{u}?aircraft={w.aircraft_pk}", url + " ?aircraft"))
            found.append(
                _built(
                    "leaderboard",
                    lambda w, u=url: f"{u}?aircraft={w.aircraft_pk}&tour=all&sort=name",
                    url + " ?aircraft all-time by name",
                    rare_sort=True,
                )
            )

    found += _sorts("mission-list", lambda w: "/missions/?", list(mission_reads.SORT_FIELDS), "/missions/", ("-date",))
    for query in (
        "tour=all",
        "period=7",
        "winner=redfor",
        "winner=none",
        "empty=1",
        "q=mission",
        "tour=all&period=30&sort=-players",
    ):
        found.append(_fixed("mission-list", f"/missions/?{query}", rare_sort="sort=" in query))
    found += _sorts(
        "mission-detail",
        lambda w: f"/missions/{w.mission_pk}/?",
        list(mission_reads.SORTIE_SORT_FIELDS),
        "/missions/<id>/",
    )

    found += _sorts(
        "player-search", lambda w: "/players/?", list(player_reads.PLAYER_SORTS), "/players/", PLAYER_LIST_INDEXED
    )
    found.append(_fixed("player-search", "/players/?q=Pilot&sort=-sorties"))
    found += _sorts(
        "player-detail",
        lambda w: f"/players/{w.player_pk}/?tour=all&",
        list(player_reads.AIRCRAFT_SORTS),
        "/players/<id>/",
    )

    def sorties(w: SeededWorld) -> str:
        return f"/players/{w.player_pk}/sorties/?"

    found += _sorts(
        "player-sorties",
        lambda w: sorties(w) + "tour=all&",
        list(sortie_reads.SORT_FIELDS),
        "sorties all-time",
        ("-date",),
    )
    found += _sorts("player-sorties", sorties, list(sortie_reads.SORT_FIELDS), "sorties current tour", ("-date",))
    found.append(
        _built("player-sorties", lambda w: sorties(w) + f"aircraft={w.aircraft_pk}&tour=all", "sorties ?aircraft")
    )
    for outcome in Outcome.values:
        found.append(
            _built(
                "player-sorties",
                lambda w, o=outcome: sorties(w) + f"outcome={o}&tour=all",
                f"sorties outcome={outcome}",
            )
        )
    found.append(
        _built(
            "player-sorties",
            lambda w: sorties(w) + "role=pilot&combat_role=attack&tour=all",
            "sorties role+combat_role",
        )
    )

    # the site-wide sortie list (`/sorties/`): newest first is the indexed order; every other sort is a rare one
    found += _sorts(
        "sortie-list",
        lambda w: "/sorties/?tour=all&",
        list(sortie_reads.SORT_FIELDS),
        "all sorties all-time",
        ("-date",),
    )
    found += _sorts(
        "sortie-list", lambda w: "/sorties/?", list(sortie_reads.SORT_FIELDS), "all sorties current tour", ("-date",)
    )
    for query in (
        "aircraft={aircraft}&tour=all",
        "q=Pilot&tour=all",
        "combat_role=attack&tour=all",
        "seat=gunner&tour=all",
        "seat=any&tour=all",
        "outcome=landed&tour=all",
        "outcome=landed",
    ):
        found.append(
            _built(
                "sortie-list",
                lambda w, q=query: "/sorties/?" + q.format(aircraft=w.aircraft_pk),
                "all sorties ?" + query.replace("{aircraft}", "<aircraft>"),
            )
        )

    def killboard(w: SeededWorld) -> str:
        return f"/players/{w.player_pk}/killboard/?"

    found += _sorts(
        "player-killboard",
        lambda w: killboard(w) + "tour=all&",
        list(board_reads.KILLBOARD_SORTS),
        "killboard all-time",
        ("-kills",),
    )
    found += _sorts(
        "player-killboard", killboard, list(board_reads.KILLBOARD_SORTS), "killboard current tour", ("-kills",)
    )
    found.append(_built("player-streaks", lambda w: f"/players/{w.player_pk}/streaks/?tour=all", "streaks all-time"))
    found.append(
        _built(
            "player-streak-runs", lambda w: f"/players/{w.player_pk}/streaks/history/?tour=all", "streak runs all-time"
        )
    )
    found.append(_fixed("achievement-holders", "/achievements/flight_hours/?tier=1"))

    found += _sorts("aircraft-list", lambda w: "/aircraft/?", list(aircraft_reads.AIRCRAFT_SORTS), "/aircraft/")
    for role in (AircraftRole.AIR_SUPERIORITY, AircraftRole.ATTACK):
        found.append(_fixed("aircraft-list", f"/aircraft/?role={role.value}"))
        found.append(_fixed("aircraft-list", f"/aircraft/?role={role.value}&tour=all"))

    def aircraft(w: SeededWorld) -> str:
        return f"/aircraft/{w.aircraft_pk}/?"

    found += _sorts(
        "aircraft-detail", lambda w: aircraft(w) + "tour=all&", list(aircraft_reads.MATCHUP_SORTS), "aircraft all-time"
    )
    found.append(_built("aircraft-detail", lambda w: aircraft(w) + "tour=all&intercept=1", "aircraft intercept"))
    # The loadout (payload) and weapon-mod tables sort apart from the matchups (`lsort`, `msort`), per role and tour.
    for role in (AircraftRole.AIR_SUPERIORITY, AircraftRole.ATTACK):
        for tour in ("", "tour=all&"):
            found.append(
                _built(
                    "aircraft-detail",
                    lambda w, r=role, t=tour: f"{aircraft(w)}{t}role={r.value}",
                    f"aircraft role={role.value} {tour}".strip(),
                )
            )
    for key in aircraft_reads.LOADOUT_SORTS:
        for prefix in ("", "-"):
            found.append(
                _built(
                    "aircraft-detail",
                    lambda w, k=key, p=prefix: f"{aircraft(w)}tour=all&lsort={p}{k}",
                    f"aircraft lsort={prefix}{key}",
                )
            )
    for key in aircraft_reads.MOD_SORTS:
        for prefix in ("", "-"):
            found.append(
                _built(
                    "aircraft-detail",
                    lambda w, k=key, p=prefix: f"{aircraft(w)}tour=all&msort={p}{k}",
                    f"aircraft msort={prefix}{key}",
                )
            )
    for state in ("with", "without"):
        found.append(
            _built(
                "aircraft-detail",
                lambda w, s=state: _mod_filtered(w, s),
                f"aircraft with a weapon-mod filter ({state}), when the seeded types have one",
            )
        )
    return found


def _mod_filtered(world: SeededWorld, state: str) -> str:
    """The detail page of a seeded type that has significant weapon mods, filtered by all of them; the plain page when
    no seeded type has any (the filter then has nothing to select)."""
    for pk in world.aircraft_pks:
        mods = aircraft_reads.significant_mods(GameObject.objects.get(pk=pk))
        if mods:
            query = "&".join(f"{aircraft_reads.mod_param(m.mod_id)}={state}" for m in mods)
            return f"/aircraft/{pk}/?tour=all&{query}"
    return f"/aircraft/{world.aircraft_pk}/?tour=all"


_PLACEHOLDER = SeededWorld(0, 0, 0, 0, "", 0, 0, [], [], [], [], [])

VARIANTS: tuple[Variant, ...] = tuple(_variants())
