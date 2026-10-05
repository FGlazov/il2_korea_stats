"""Optional table columns of the player, mission and aircraft lists (maintainer request 2026-10-04).

Each list keeps its default columns in its template; a visitor can add the columns registered here with a "Columns"
control. The choice lives in the URL as `?cols=elo_jet,kd` (repeated `cols=` parameters, which the no-JS form
submits, work too), so links are shareable and the page cache, keyed on the full URL, stays correct. Unknown keys are
ignored; the columns always appear in the registry's order, whatever the order in the URL. Every column's `key` is
also its `?sort=` key (the pages' sort whitelists in `il2ks.queries`; a test keeps the two in step).

Cells are plain text computed from counters the row already has: no extra query per row, no aggregation (TD-22).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from django.http import QueryDict
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.utils.translation import gettext_lazy as _

from il2ks.db.models import AircraftCounters, CombatRole, Mission, Player, PlayerSortie
from il2ks.queries.tours import tour_title
from il2ks.web import column_hints, display
from il2ks.web.display import Label

COLS_PARAM = "cols"
MAX_COLS_VALUES = 40  # the longest list of keys read from the query string (there are fewer columns than that)
MAX_COL_KEY_LENGTH = 32


@dataclass(frozen=True, slots=True)
class Column[T]:
    """An optional column: `key` (also its sort key), the header `label`, a `cell` that renders a row's value as text
    (HTML-safe `<time>` elements allowed) and an optional `hint` (else the description registered under `key` in
    `web.column_hints`), shown as a tooltip in the picker and on the column's header (`description`)."""

    key: str
    label: Label
    cell: Callable[[T], str | SafeString]
    hint: Label = ""
    numeric: bool = True
    sortable: bool = True
    """False: the header is plain (no `?sort=` key); only a column whose value is not stored with the row."""

    @property
    def description(self) -> str:
        """The text shown for the column (picker tooltip and header): its own `hint`, else the shared description
        registered under its key in `web.column_hints` (so `elo_jet`, `kd`... need no `hint` here), else nothing."""
        return column_hints.hint_text(self.hint) or column_hints.hint_text(self.key)


def requested_keys(params: QueryDict) -> frozenset[str]:
    """The column keys named by `?cols=` (comma separated and/or repeated), unvalidated."""
    return frozenset(
        key
        for raw in params.getlist(COLS_PARAM)[:MAX_COLS_VALUES]
        for key in (part.strip() for part in raw.split(","))
        if 0 < len(key) <= MAX_COL_KEY_LENGTH
    )


def chosen[T](params: QueryDict, available: Sequence[Column[T]]) -> list[Column[T]]:
    """The columns of `available` that `?cols=` asks for, in registry order; unknown keys are ignored."""
    wanted = requested_keys(params)
    return [column for column in available if column.key in wanted]


# --- players (all time: the `Player` row; Elo and the per-tour split exist only there) --------------------------------
def _elo(rating: float, games: int) -> str:
    return display.num(rating) if games else display.DASH  # 1500 with no encounter is only the starting value


def _accuracy(hits: int, rounds: int) -> str:
    return display.percent(hits, rounds, 1)


PLAYER_COLUMNS: tuple[Column[Player], ...] = (
    Column("elo_jet", _("Elo (jet)"), lambda p: _elo(p.elo_jet, p.elo_jet_games)),
    Column("elo_prop", _("Elo (prop)"), lambda p: _elo(p.elo_prop, p.elo_prop_games)),
    Column("kd", _("K/D"), lambda p: display.ratio(p.kills_air, p.deaths)),
    Column("kl", _("K/L"), lambda p: display.ratio(p.kills_air, p.planes_lost)),
    Column("survival", _("Survival"), lambda p: display.percent(max(p.sorties - p.deaths, 0), p.sorties)),
    Column("kills_air_pvp", _("Air kills (PvP)"), lambda p: display.num(p.kills_air_pvp)),
    Column("score_air", _("Air score"), lambda p: display.num(p.score_air)),
    Column("score_ground", _("Ground score"), lambda p: display.num(p.score_ground)),
    Column(
        "ground_hour",
        _("Attack proficiency"),
        lambda p: display.per_hour(p.score_ground_attack, p.time_on_target_s, 1),
    ),
    Column("planes_lost", _("Aircraft lost"), lambda p: display.num(p.planes_lost)),
    Column("assists", _("Assists"), lambda p: display.num(p.assists)),
    Column("assists_air", _("Air assists"), lambda p: display.num(p.assists_air)),
    Column("assists_ground", _("Ground assists"), lambda p: display.num(p.assists_ground)),
    Column("accuracy", _("Gun accuracy"), lambda p: _accuracy(p.accuracy_hits, p.accuracy_rounds)),
    Column(
        "accuracy_air",
        _("Air accuracy"),
        lambda p: _accuracy(p.accuracy_air_hits, p.accuracy_air_rounds),
    ),
    Column(
        "accuracy_ground",
        _("Ground accuracy"),
        lambda p: _accuracy(p.accuracy_ground_hits, p.accuracy_ground_rounds),
    ),
    Column("friendly_kills", _("Friendly kills"), lambda p: display.num(p.friendly_kills)),
    Column("first_seen", _("First seen"), lambda p: display.time_element(p.first_seen, "date")),
)


# --- missions ------------------------------------------------------------------------------------------------------
MISSION_COLUMNS: tuple[Column[Mission], ...] = (
    Column("friendly_kills", _("Friendly kills"), lambda m: display.num(m.friendly_kills)),
    Column("tour", _("Tour"), lambda m: tour_title(m.tour.title) if m.tour is not None else display.DASH),
    Column("ended", _("Ended"), lambda m: display.time_element(m.ended_at, "datetime")),
    Column("redfor_sorties", _("REDFOR sorties"), lambda m: display.num(m.redfor_sorties)),
    Column("blufor_sorties", _("BLUFOR sorties"), lambda m: display.num(m.blufor_sorties)),
    Column("sorties_per_player", _("Sorties per pilot"), lambda m: display.ratio(m.sorties_total, m.players_total, 1)),
)


# --- a player's sortie list ----------------------------------------------------------------------------------------
def _mission_link(s: PlayerSortie) -> SafeString:
    """The mission's name linking to its page (the mission is loaded with the row: no extra query)."""
    url = reverse("web:mission-detail", args=[s.mission_id])
    return format_html('<a href="{}">{}</a>', url, display.mission_name(s.mission.mission_file))


SORTIE_COLUMNS: tuple[Column[PlayerSortie], ...] = (
    Column("mission", _("Mission"), _mission_link, numeric=False),
    Column("kills_air_pvp", _("Air kills (PvP)"), lambda s: display.num(s.kills_air_pvp)),
    Column("kills_air_ai", _("Air kills (AI)"), lambda s: display.num(s.kills_air_ai)),
    Column("assists_air", _("Air assists"), lambda s: display.num(s.assists_air)),
    Column("assists_ground", _("Ground assists"), lambda s: display.num(s.assists_ground)),
    Column("friendly_kills", _("Friendly kills"), lambda s: display.num(s.friendly_kills)),
    Column("air_points", _("Air score"), lambda s: display.num(s.air_points)),
    Column("ground_points", _("Ground score"), lambda s: display.num(s.ground_points)),
    Column(
        "time_on_target",
        _("Time on target"),
        lambda s: display.duration(s.time_on_target_s) if s.time_on_target_s else display.DASH,
    ),
    Column("payload", _("Loadout"), lambda s: s.payload_name or display.DASH, numeric=False),
    Column("takeoffs", _("Takeoffs"), lambda s: display.num(s.takeoffs)),
    Column("landings", _("Landings"), lambda s: display.num(s.landings)),
    Column(
        "accuracy",
        _("Gun accuracy"),
        lambda s: _accuracy(s.gun_hits_air + s.gun_hits_ground, s.rounds_fired or 0),
        _("Gun hits per round fired; a dash when the rounds fired are unknown"),
    ),
    Column(
        "accuracy_air",
        _("Air accuracy"),
        lambda s: (
            _accuracy(s.gun_hits_air, s.rounds_fired or 0)
            if s.combat_role == CombatRole.AIR_SUPERIORITY
            else display.DASH
        ),
        _("Gun hits on aircraft per round fired; air superiority sorties with known rounds only"),
    ),
    Column(
        "accuracy_ground",
        _("Ground accuracy"),
        lambda s: (
            _accuracy(s.gun_hits_ground, s.rounds_fired or 0) if s.combat_role == CombatRole.ATTACK else display.DASH
        ),
        _("Gun hits on ground targets per round fired; attack sorties with known rounds only"),
    ),
)


# --- the sortie tables of a mission page ---------------------------------------------------------------------------
# The default columns (time, pilot, aircraft, role, outcome, fate, kills, assists, flight time) stay in the template;
# these are the extras: the player sortie list's, minus the mission (this page), plus the damage taken (a default
# column there).
MISSION_SORTIE_COLUMNS: tuple[Column[PlayerSortie], ...] = (
    Column("damage_taken", _("Damage taken"), lambda s: f"{round(s.damage_taken * 100)}%"),
    *(column for column in SORTIE_COLUMNS if column.key != "mission"),
)


# --- aircraft (all time or one tour: both stats rows have the same counters) ---------------------------------------
class AircraftListRow(Protocol):
    """What an aircraft list cell reads: the row's counters (`AircraftStats` or `TourAircraftStats`), the average gun
    hits to destroy the type (all time) and the sorties without a death (`web.views.aircraft.AircraftRow`)."""

    @property
    def stats(self) -> AircraftCounters: ...

    @property
    def hits_average(self) -> str: ...


# The default columns (maintainer 2026-10-05) stay in `aircraft/list.html`: aircraft, sorties, Elo, K/L, survival and
# attack proficiency (ground score per hour on target). Everything else the list ever showed is optional.
AIRCRAFT_COLUMNS: tuple[Column[AircraftListRow], ...] = (
    Column("pilots", _("Pilots"), lambda a: display.num(a.stats.pilots)),
    Column("flight_time_s", _("Flight time"), lambda a: display.duration(a.stats.flight_time_s), "flight_time"),
    Column("kills_air", _("Air kills"), lambda a: display.num(a.stats.kills_air)),
    Column("kills_ground", _("Ground kills"), lambda a: display.num(a.stats.kills_ground)),
    Column("deaths", _("Deaths"), lambda a: display.num(a.stats.deaths)),
    Column("planes_lost", _("Aircraft lost"), lambda a: display.num(a.stats.planes_lost)),
    Column("kd", _("K/D"), lambda a: display.ratio(a.stats.kills_air, a.stats.deaths)),
    Column(
        "attack_share",
        _("Attack sorties"),
        lambda a: display.percent(a.stats.attack_sorties, a.stats.sorties),
        "attack_sorties",
    ),
    Column(
        "hits",
        _("Hits to destroy"),
        lambda a: a.hits_average,
        _(
            "Average number of gun hits that destroyed it, counted over all time. Only kills where one attacker did "
            "all the damage are included, and bombs and rockets are left out."
        ),
        sortable=False,
    ),
    Column("kills_air_pvp", _("Air kills (PvP)"), lambda a: display.num(a.stats.kills_air_pvp)),
    Column("kills_per_hour", _("Air kills/h"), lambda a: display.per_hour(a.stats.kills_air, a.stats.flight_time_s)),
    Column("assists", _("Assists"), lambda a: display.num(a.stats.assists)),
    Column("bailouts", _("Bailouts"), lambda a: display.num(a.stats.bailouts)),
    Column("friendly_kills", _("Friendly kills"), lambda a: display.num(a.stats.friendly_kills)),
    Column("score_air", _("Air score"), lambda a: display.num(a.stats.score_air)),
    Column("score_ground", _("Ground score"), lambda a: display.num(a.stats.score_ground)),
    Column(
        "sortie_length",
        _("Sortie length"),
        lambda a: display.duration(a.stats.flight_time_s / a.stats.sorties) if a.stats.sorties else display.DASH,
        _("Average flight time per sortie"),
    ),
    Column("sorties_per_pilot", _("Sorties per pilot"), lambda a: display.ratio(a.stats.sorties, a.stats.pilots, 1)),
    Column("accuracy", _("Gun accuracy"), lambda a: _accuracy(a.stats.accuracy_hits, a.stats.accuracy_rounds)),
    Column(
        "accuracy_air",
        _("Air accuracy"),
        lambda a: _accuracy(a.stats.accuracy_air_hits, a.stats.accuracy_air_rounds),
    ),
    Column(
        "accuracy_ground",
        _("Ground accuracy"),
        lambda a: _accuracy(a.stats.accuracy_ground_hits, a.stats.accuracy_ground_rounds),
    ),
)
