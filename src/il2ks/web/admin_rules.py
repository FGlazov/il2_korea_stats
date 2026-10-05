"""The admin's pages for the game rules of `il2ks.toml` (Scoring, Tours, Rules, Leaderboards; maintainer decision
2026-10-05, FR-ADM-7, doc 16): the rows of a form, and reading a posted one.

Every field is a text input (a select for yes/no) whose blank means "use `il2ks.toml` (or the built-in default)", which
the page shows as the placeholder. The values are checked by `il2ks.rule_settings.validate`, i.e. by the parser that
reads the file. Saving, wanted vs applied and the pending state are `il2ks.ingest.rule_store`."""

from collections.abc import Mapping
from dataclasses import dataclass

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from il2ks.config import RuleSet
from il2ks.core.tours import parse_mode
from il2ks.db.models import SiteSettings
from il2ks.ingest.rule_store import pending_fields
from il2ks.rule_settings import Page, RuleField, base_value, fields_of, sanitize, validate

LABELS = {
    "score.air_kill_pvp": _("Air kill, a player's aircraft (points)"),
    "score.air_kill_ai": _("Air kill, an AI aircraft (points)"),
    "score.air_assist": _("Air assist (points)"),
    "score.ground_tank": _("Ground kill: tank (points)"),
    "score.ground_vehicle": _("Ground kill: vehicle (points)"),
    "score.ground_artillery": _("Ground kill: artillery (points)"),
    "score.ground_aaa": _("Ground kill: anti-aircraft gun (points)"),
    "score.ground_ship": _("Ground kill: ship (points)"),
    "score.ground_train": _("Ground kill: train (points)"),
    "score.ground_building": _("Ground kill: building (points)"),
    "score.ground_parked_aircraft": _("Ground kill: parked aircraft (points)"),
    "score.ground_other": _("Ground kill: anything else, such as fences and barrels (points)"),
    "score.penalty_death_pct": _("Pilot died: share of the sortie's score lost (percent)"),
    "score.penalty_capture_pct": _("Pilot captured: share of the sortie's score lost (percent)"),
    "score.penalty_plane_lost_pct": _("Aircraft lost without a death or capture: share of the score lost (percent)"),
    "score.penalty_early_bailout": _("Suspected early bailout: points taken off"),
    "score.penalty_friendly_kill": _("Friendly kill: points taken off for each"),
    "score.penalty_friendly_kill_cap": _("Friendly kills that cost points, at most per sortie"),
    "killboard.assists": _("Show assists in their own column of the killboard"),
    "ratings.start": _("Elo: rating of a pilot with no games"),
    "ratings.k": _("Elo: largest change of one game (K factor)"),
    "ratings.cross_pool_weight": _("Elo: weight of a propeller kill on a jet"),
    "tours.mode": _("How long a tour lasts"),
    "tours.start": _("First day of tour 1 (only for tours of N days)"),
    "tours.timezone": _("Time zone where a tour begins and ends"),
    "rules.credit_rams": _("Credit each pilot with a kill when two enemy aircraft collide"),
    "rules.ram_window_s": _("Ram: the two losses must be this close in time (seconds)"),
    "rules.ram_distance_m": _("Ram: and this close in space (meters)"),
    "score.min_sorties": _("Score and kill boards: sorties a pilot needs"),
    "score.min_elo_games": _("Elo boards: encounters a pilot needs"),
    "score.min_attack_sorties": _("Ground score per hour: attack sorties a pilot needs"),
    "score.min_time_on_target_minutes": _("Ground score per hour and tank busting: minutes on target a pilot needs"),
    "score.min_air_superiority_sorties": _("Interception: air superiority sorties a pilot needs"),
    "score.min_air_superiority_minutes": _("Interception: minutes of air superiority flight a pilot needs"),
    "marks.min_sorties": _("Marks for the best pilots: sorties a pilot needs to be compared"),
}

HELP = {
    "tours.mode": _("monthly, days:14 (blocks of 14 days counted from the first day) or manual"),
    "tours.start": _("A date like 2026-10-01. Required for days:N, ignored otherwise."),
    "tours.timezone": _("An IANA name like Europe/Berlin. Empty: the server's time zone."),
}

EFFECT_TEXT = {  # lazy: read per request, in the admin's language (never `str()` at import time)
    "rescore": _("Re-scores every sortie in the background"),
    "retour": _("Moves the missions into other tours in the background"),
    "reprocess": _("New missions at once; older ones after a reprocess"),
    "display": _("Applies at once"),
}
# For a `rescore` field of the Leaderboards page (`score.min_elo_games`): it re-scores nothing, the rebuild recomputes
# the all-time Elo with it (and the Elo boards and marks follow, since the applied value changes with that rebuild).
RECOMPUTE_TEXT = _("Recomputed in the background by the next rebuild")


def effect_text(field: RuleField) -> str:
    """When a change of this field takes hold, in the active language."""
    if field.effect == "rescore" and field.page == "leaderboards":
        return str(RECOMPUTE_TEXT)
    return str(EFFECT_TEXT[field.effect])


def group_title(field: RuleField) -> str:
    """The heading a field sits under on its page ('' = none)."""
    if field.section == "score" and field.page == "scoring":
        return str(_("Penalties")) if field.name.startswith("penalty_") else str(_("Points"))
    return {
        "killboard": str(_("Killboard")),
        "ratings": str(_("Elo ratings")),
        "rules": str(_("Rams")),
        "replay": str(_("Fine rules of how a flight is read (change only if results look wrong)")),
    }.get(field.section, "")


@dataclass(frozen=True, slots=True)
class Row:
    key: str
    label: str
    hint: str
    kind: str  # "bool" is a select, anything else a text input
    typed: str  # what the input shows: the admin's value, or what was posted
    default: str  # what the file (or the built-in default) says
    effect: str
    pending: bool


@dataclass(frozen=True, slots=True)
class Group:
    title: str
    rows: list[Row]


def base_rules() -> RuleSet:
    """The file's rules as the web process read them at start (the built-in defaults in tests)."""
    configured = getattr(settings, "IL2KS_RULES", None)
    return configured if isinstance(configured, RuleSet) else RuleSet()


def tour_mode() -> str:
    """The tour mode in force ("monthly", "days" or "manual"): the admin's applied choice, else the file's."""
    row = SiteSettings.objects.filter(pk=1).values_list("rule_settings_applied", flat=True).first()
    chosen = sanitize(row).get("tours.mode")
    if isinstance(chosen, str):
        try:
            return parse_mode(chosen)[0]
        except ValueError:
            pass
    return str(getattr(settings, "IL2KS_TOUR_MODE", "monthly"))


def show(value: object, *, bool_words: bool = False) -> str:
    if isinstance(value, bool):
        return ("on" if value else "off") if bool_words else (str(_("on")) if value else str(_("off")))
    if isinstance(value, float):
        return f"{value:g}"
    return "" if value is None else str(value)


def build_groups(page: Page, site: SiteSettings, posted: Mapping[str, str] | None = None) -> list[Group]:
    """The rows of a page, in order and grouped. `posted` (a rejected form) puts back what was typed."""
    base = base_rules()
    wanted = sanitize(site.rule_settings)
    waiting = {f.key for f in pending_fields()}
    groups: dict[str, list[Row]] = {}
    for field in fields_of(page):
        typed = (
            posted.get(field.key, "")
            if posted is not None
            else (show(wanted[field.key], bool_words=True) if field.key in wanted else "")
        )
        label = str(LABELS[field.key]) if field.key in LABELS else field.name  # fine replay rules: the file's key
        row = Row(
            key=field.key,
            label=label,
            hint=str(HELP[field.key]) if field.key in HELP else "",
            kind="bool" if field.kind == "bool" else "text",
            typed=typed,
            default=show(base_value(base, field)),
            effect=effect_text(field),
            pending=field.key in waiting,
        )
        groups.setdefault(group_title(field), []).append(row)
    return [Group(title, rows) for title, rows in groups.items()]


@dataclass(frozen=True, slots=True)
class Posted:
    fields: tuple[RuleField, ...]
    overrides: dict[str, object]
    problems: list[str]
    texts: dict[str, str]


def read_post(page: Page, post: Mapping[str, str]) -> Posted:
    """The overrides a posted page describes and what stops it from being saved."""
    fields = fields_of(page)
    texts = {f.key: post.get(f.key, "") for f in fields}
    overrides, problems = validate(base_rules(), texts)
    return Posted(fields, overrides, problems, texts)
