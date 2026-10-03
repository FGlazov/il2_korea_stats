"""View models of the sortie detail page (FR-WEB-6): the stored JSON and rows, shaped for the templates.

Pure presentation (TD-22): nothing here queries the database or computes game rules. The page's reads are in
`il2ks.queries.sorties`; `build_detail` only reshapes what they returned. The JSON shapes are those `ingest.persist`
writes (doc 14): `ammo` = {loaded, left, used, hits}, `damage_breakdown` and `timeline` link counterpart sorties by
database id, positions are `[x, y, z]`, timeline entries carry an ISO UTC `at`.

Hidden players (FR-ADM-3) never reach a template by name: `Lookup.who` turns them into `Who(kind="hidden")`.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, cast

from django.utils.translation import get_language, gettext_lazy
from django.utils.translation import gettext as _

from il2ks.core.catalog.loader import GROUND_CATEGORIES
from il2ks.db.models import GameObject, Kill, PlayerSortie
from il2ks.web import display, icons, object_names

type Json = Mapping[str, object]
type WhoKind = Literal["player", "hidden", "ai"]

TIMELINE_GROUP_MIN = 3  # consecutive ground kills folded into one row from this many on
TIMELINE_HEAD_ROWS = 80  # rows shown at once; the rest sits in a "show all" element
DAMAGE_HEAD_ROWS = 15
AIR_CLASSES = frozenset({"fighter", "attacker", "bomber", "transport"})

# What an AI counterpart is, by catalog class (the log name alone says little).
CLASS_LABELS: Mapping[str, str] = {
    "fighter": gettext_lazy("AI aircraft"),
    "attacker": gettext_lazy("AI aircraft"),
    "bomber": gettext_lazy("AI aircraft"),
    "transport": gettext_lazy("AI aircraft"),
    "gunner": gettext_lazy("AI gunner"),
    "aaa": gettext_lazy("Anti-aircraft"),
    "tank": gettext_lazy("Tank"),
    "vehicle": gettext_lazy("Vehicle"),
    "ship": gettext_lazy("Ship"),
    "static": gettext_lazy("Static object"),
}

GROUND_LABELS: Mapping[str, str] = {
    "tank": gettext_lazy("Tanks"),
    "vehicle": gettext_lazy("Vehicles"),
    "artillery": gettext_lazy("Artillery"),
    "aaa": gettext_lazy("Anti-aircraft"),
    "ship": gettext_lazy("Ships"),
    "train": gettext_lazy("Trains"),
    "building": gettext_lazy("Buildings"),
    "parked_aircraft": gettext_lazy("Parked aircraft"),
    "other": gettext_lazy("Other objects"),
}
GROUND_ICONS: Mapping[str, str] = {
    "tank": "ground/tank",
    "vehicle": "ground/vehicle",
    "artillery": "ground/artillery",
    "aaa": "ground/aaa",
    "ship": "ground/ship",
    "train": "ground/train",
    "building": "ground/building",
    "parked_aircraft": "ground/parked-aircraft",
    "other": "ground/other-static",
}

EVENT_LABELS: Mapping[str, str] = {
    "spawn": gettext_lazy("Spawned"),
    "takeoff": gettext_lazy("Took off"),
    "landing": gettext_lazy("Landed"),
    "kill": gettext_lazy("Kill"),
    "assist": gettext_lazy("Assist"),
    "friendly_fire": gettext_lazy("Friendly fire"),
    "damaged": gettext_lazy("Damaged"),
    "shot_down": gettext_lazy("Shot down"),
    "destroyed": gettext_lazy("Aircraft lost"),
    "killed": gettext_lazy("Killed"),
    "died": gettext_lazy("Died"),
    "bailout": gettext_lazy("Bailed out"),
    "disconnect": gettext_lazy("Left the server"),
    "sortie_end": gettext_lazy("Sortie ended"),
    "bomb_release": gettext_lazy("Bomb release"),
    "rocket_salvo": gettext_lazy("Rocket salvo"),
}
EVENT_ICONS: Mapping[str, str] = {
    "spawn": "event/spawn",
    "takeoff": "event/takeoff",
    "landing": "event/landing",
    "kill": "event/kill-air",
    "assist": "event/assist",
    "friendly_fire": "event/friendly-fire",
    "damaged": "event/damaged",
    "shot_down": "event/destroyed",
    "destroyed": "event/destroyed",
    "killed": "outcome/dead",
    "died": "outcome/dead",
    "bailout": "event/bailout",
    "disconnect": "event/disconnect",
    "sortie_end": "event/sortie-end",
    "bomb_release": "event/bomb-release",
    "rocket_salvo": "event/rocket-salvo",
}
# Row accent in the timeline: "good" for what the pilot achieved, "bad" for what was lost or went wrong.
EVENT_TONES: Mapping[str, str] = {
    "kill": "good",
    "assist": "good",
    "friendly_fire": "bad",
    "shot_down": "bad",
    "destroyed": "bad",
    "killed": "bad",
    "died": "bad",
    "bailout": "warn",
    "disconnect": "warn",
}
SPAWN_LABELS: Mapping[str, str] = {
    "air": gettext_lazy("Air start"),
    "runway": gettext_lazy("Runway"),
    "parking": gettext_lazy("Parking"),
}
AMMO_LABELS: Mapping[str, str] = {
    "bullets": gettext_lazy("Machine-gun rounds"),
    "shells": gettext_lazy("Cannon shells"),
    "bombs": gettext_lazy("Bombs"),
    "rockets": gettext_lazy("Rockets"),
}
VIA_NOTES: Mapping[str, str] = {
    "abandoned_aircraft": gettext_lazy("after the pilot bailed out"),
    "disconnect": gettext_lazy("after the pilot left the server"),
}


# --- JSON access (the fields are `object`: narrow, never assume) --------------------------------------------------
def _dict(value: object) -> Json:
    if isinstance(value, dict):
        return {str(k): v for k, v in cast("dict[object, object]", value).items()}
    return {}


def _list(value: object) -> list[object]:
    return list(cast("list[object]", value)) if isinstance(value, list) else []


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _float(value: object) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _pos(value: object) -> str:
    """'x 12,345  z -6,789  alt 900 m' for a stored [x, y, z], or ''."""
    coords = _list(value)
    if len(coords) != 3:
        return ""
    x, y, z = (_float(c) for c in coords)
    return _("x %(x)s, z %(z)s, altitude %(y)s m") % {"x": display.num(x), "z": display.num(z), "y": display.num(y)}


def _when(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


# --- who ----------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Who:
    """A counterpart as the page shows it (template `il2ks/sorties/parts/who.html`).

    kind:     "player" (named, linked), "hidden" (a hidden player: no name, no link) or "ai" (AI, ground object).
    name:     the player's name at the time, or the object's display name for "ai".
    player_id, sortie_id: link targets of a "player" (None otherwise).
    aircraft: display name of the aircraft or object type; icon: its icon name (`{% icon %}`); label: what an AI
    counterpart is ("AI gunner", "Anti-aircraft", ...), '' for players."""

    kind: WhoKind
    name: str = ""
    player_id: int | None = None
    sortie_id: int | None = None
    aircraft: str = ""
    icon: str = ""
    label: str = ""


def _object_icon(obj: GameObject | None) -> str:
    if obj is None:
        return ""
    if obj.cls in AIR_CLASSES:
        return icons.aircraft_icon_name(obj.log_name, obj.propulsion)
    if obj.ground_category in GROUND_ICONS:
        return GROUND_ICONS[obj.ground_category]
    return ""


@dataclass(frozen=True, slots=True)
class Lookup:
    """The rows the JSON refers to: counterpart sorties by id and game objects by log name."""

    sorties: Mapping[int, PlayerSortie]
    objects: Mapping[str, GameObject]

    def object_name(self, log_name: str) -> str:
        obj = self.objects.get(log_name)
        return object_names.name_of(obj, get_language() or "en") if obj is not None else log_name

    def object_class(self, log_name: str) -> str:
        obj = self.objects.get(log_name)
        return obj.cls if obj is not None else ""

    def who(self, sortie_id: int | None, object_type: str) -> Who:
        """A player sortie (named, or hidden), else an AI or ground object by its log name."""
        row = self.sorties.get(sortie_id) if sortie_id is not None else None
        if row is not None:
            obj = row.aircraft
            name = object_names.name_of(obj, get_language() or "en")
            icon = icons.aircraft_icon_name(obj.log_name, obj.propulsion)
            if row.player.is_hidden:
                return Who("hidden", aircraft=name, icon=icon)
            return Who("player", row.name_at_time, row.player_id, row.pk, name, icon)
        obj = self.objects.get(object_type)
        label = str(CLASS_LABELS.get(obj.cls, "")) if obj is not None else ""
        return Who(
            "ai",
            self.object_name(object_type),
            aircraft=self.object_name(object_type),
            icon=_object_icon(obj),
            label=label,
        )

    def counterpart(self, raw: object) -> Who | None:
        """A stored counterpart {object_type, coalition, sortie_id}."""
        data = _dict(raw)
        if not data:
            return None
        return self.who(_int(data.get("sortie_id")), _str(data.get("object_type")))


def counterpart_sortie_ids(sortie: PlayerSortie, kills: Iterable[Kill]) -> set[int]:
    """Every counterpart sortie id the page will show: kills made and suffered, damage exchanges, timeline."""
    ids: set[int] = set()
    for kill in kills:
        ids.update((kill.killer_sortie_id, kill.victim_sortie_id))
    for entry in sortie.damage_breakdown:
        found = _int(_dict(_dict(entry).get("counterpart")).get("sortie_id"))
        if found is not None:
            ids.add(found)
    for entry in sortie.timeline:
        found = _int(_dict(_dict(entry).get("counterpart")).get("sortie_id"))
        if found is not None:
            ids.add(found)
    ids.discard(sortie.pk)
    return ids


def counterpart_object_types(sortie: PlayerSortie) -> set[str]:
    """Log names of the AI and ground objects named in the damage exchanges and the timeline."""
    names: set[str] = set()
    for entry in sortie.damage_breakdown:
        names.add(_str(_dict(_dict(entry).get("counterpart")).get("object_type")))
    for item in sortie.timeline:
        entry = _dict(item)
        names.add(_str(_dict(entry.get("counterpart")).get("object_type")))
        if _str(entry.get("kind")) in ("kill", "assist", "friendly_fire"):
            names.add(_str(entry.get("detail")))
    names.discard("")
    return names


# --- clock --------------------------------------------------------------------------------------------------------
def since(start: datetime, moment: datetime) -> str:
    """'+12:34' (or '+1:02:03') after the sortie's spawn: the flight's own clock."""
    seconds = max(0, round((moment - start).total_seconds()))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"+{hours}:{minutes:02d}:{secs:02d}" if hours else f"+{minutes}:{secs:02d}"


def utc_clock(moment: datetime) -> str:
    """'20:41:07' in UTC."""
    return moment.astimezone(UTC).strftime("%H:%M:%S")


# --- kills, assists, shot down by ---------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class KillRow:
    """A kill, assist or friendly kill: when (sortie clock and UTC), who, and a note (how the victim was lost)."""

    clock: str
    utc: str
    who: Who
    note: str = ""
    friendly: bool = False


@dataclass(frozen=True, slots=True)
class KillerRow:
    """Who shot this sortie down (credit "kill") or only damaged it (credit "assist")."""

    clock: str
    utc: str
    who: Who
    credit: str
    note: str = ""
    friendly: bool = False


@dataclass(frozen=True, slots=True)
class GroundRow:
    category: str
    label: str
    icon: str
    count: int
    share: str


@dataclass(frozen=True, slots=True)
class GroundBreakdown:
    """Ground kills by category (`kills_ground_*`, they sum to `kills_ground`) and how many were static objects."""

    total: int
    static: int
    static_share: str
    rows: Sequence[GroundRow]


def ground_breakdown(sortie: PlayerSortie) -> GroundBreakdown:
    rows = [
        GroundRow(
            category,
            str(GROUND_LABELS[category]),
            GROUND_ICONS[category],
            count,
            display.percent(count, sortie.kills_ground),
        )
        for category in GROUND_CATEGORIES
        if (count := int(getattr(sortie, f"kills_ground_{category}"))) > 0
    ]
    return GroundBreakdown(
        sortie.kills_ground,
        sortie.kills_ground_static,
        display.percent(sortie.kills_ground_static, sortie.kills_ground),
        rows,
    )


def _kill_note(kill: Kill) -> str:
    return str(VIA_NOTES.get(kill.via, ""))


def pvp_kills(
    sortie: PlayerSortie, made: Sequence[Kill], lookup: Lookup
) -> tuple[list[KillRow], list[KillRow], list[KillRow]]:
    """(kills, assists, friendly kills) of this sortie against player sorties, from the `Kill` rows."""
    kills: list[KillRow] = []
    assists: list[KillRow] = []
    friendly: list[KillRow] = []
    for kill in made:
        row = KillRow(
            since(sortie.spawned_at, kill.time),
            utc_clock(kill.time),
            lookup.who(kill.victim_sortie_id, ""),
            _kill_note(kill),
            kill.is_friendly,
        )
        if kill.is_friendly:
            friendly.append(row)
        elif kill.credit == "assist":
            assists.append(row)
        else:
            kills.append(row)
    return kills, assists, friendly


def _is_air_object(lookup: Lookup, log_name: str) -> bool:
    return lookup.object_class(log_name) in AIR_CLASSES


def timeline_entries(sortie: PlayerSortie) -> list[Json]:
    return [_dict(item) for item in sortie.timeline]


def ai_kill_rows(sortie: PlayerSortie, lookup: Lookup) -> tuple[list[KillRow], list[KillRow]]:
    """(AI aircraft kills, AI assists) from the timeline: entries whose victim is not a player sortie."""
    air: list[KillRow] = []
    assists: list[KillRow] = []
    for entry in timeline_entries(sortie):
        kind = _str(entry.get("kind"))
        if kind not in ("kill", "assist") or _int(_dict(entry.get("counterpart")).get("sortie_id")) is not None:
            continue
        victim = _str(entry.get("detail"))
        if kind == "kill" and not _is_air_object(lookup, victim):
            continue  # ground kills are summed in the breakdown and listed in the timeline
        at = _when(entry.get("at"))
        row = KillRow(
            since(sortie.spawned_at, at) if at else "",
            utc_clock(at) if at else "",
            lookup.who(None, victim),
        )
        (air if kind == "kill" else assists).append(row)
    return air, assists


def shot_down_by(sortie: PlayerSortie, suffered: Sequence[Kill], lookup: Lookup) -> list[KillerRow]:
    """The kill and assist credits against this sortie: players from the `Kill` rows, else the AI or ground
    attacker the timeline's loss entry names. Kill first, then assists."""
    rows = [
        KillerRow(
            since(sortie.spawned_at, kill.time),
            utc_clock(kill.time),
            lookup.who(kill.killer_sortie_id, ""),
            "assist" if kill.credit == "assist" else "kill",
            _kill_note(kill),
            kill.is_friendly,
        )
        for kill in suffered
    ]
    if not any(row.credit == "kill" for row in rows):
        for entry in timeline_entries(sortie):
            if _str(entry.get("kind")) not in ("shot_down", "destroyed", "killed", "died"):
                continue
            who = lookup.counterpart(entry.get("counterpart"))
            at = _when(entry.get("at"))
            if who is not None and at is not None:
                rows.insert(0, KillerRow(since(sortie.spawned_at, at), utc_clock(at), who, "kill"))
                break
    return sorted(rows, key=lambda row: row.credit != "kill")


# --- damage -------------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class DamageRow:
    """Damage and hits exchanged with one counterpart (percent of its health, formatted)."""

    who: Who
    dealt: str
    taken: str
    hits_dealt: str
    hits_taken: str


def damage_rows(sortie: PlayerSortie, lookup: Lookup) -> list[DamageRow]:
    """Damage dealt and taken per counterpart: players first (they are the point of the table), then AI and ground
    objects; the most damage first within each."""
    parsed: list[tuple[bool, float, DamageRow]] = []
    for item in sortie.damage_breakdown:
        entry = _dict(item)
        who = lookup.counterpart(entry.get("counterpart"))
        dealt, taken = _float(entry.get("damage_dealt")), _float(entry.get("damage_taken"))
        if who is None or (dealt <= 0 and taken <= 0):
            continue
        hits_dealt, hits_taken = _int(entry.get("hits_dealt")) or 0, _int(entry.get("hits_taken")) or 0
        row = DamageRow(
            who,
            _damage(dealt, who),
            _damage(taken, who),
            display.num(hits_dealt) if hits_dealt else display.DASH,
            display.num(hits_taken) if hits_taken else display.DASH,
        )
        parsed.append((who.kind == "ai", dealt + taken, row))
    return [row for _ai, _total, row in sorted(parsed, key=lambda item: (item[0], -item[1]))]


def _damage(amount: float, who: Who) -> str:
    """A player: percent of that aircraft's health. AI and ground objects of one type are summed over every object of
    that type, so the share would pass 100% (a strafing run): shown as health units (1.0 = one whole object)."""
    if amount <= 0:
        return display.DASH
    if who.kind == "ai":
        return display.num(amount, 1)
    return _("<1%") if amount < 0.005 else display.num(amount * 100) + "%"


# --- ammo ---------------------------------------------------------------------------------------------------------
AmmoNote = Literal["", "resupplied", "no_end_record", "unreliable"]


@dataclass(frozen=True, slots=True)
class AmmoRow:
    """One ammunition kind: loaded at spawn, left at the end, used (the dash when unknown, with `note` saying why)."""

    label: str
    loaded: str
    left: str
    used: str
    note: AmmoNote


@dataclass(frozen=True, slots=True)
class HitRow:
    """Hits given and received with one ammunition type (never explosions, TD-08)."""

    name: str
    given: str
    received: str


@dataclass(frozen=True, slots=True)
class AmmoTable:
    rows: Sequence[AmmoRow]
    hits: Sequence[HitRow]
    used_unknown: AmmoNote  # why "used" is unknown for some or all kinds ('' when it is known for all)


def _count(value: object) -> str:
    number = _int(value)
    return display.num(number) if number is not None else display.DASH


def ammo_name(raw: str) -> str:
    """'BULLET_12-7_USA_API' -> '12.7 USA API' (the generic prefix dropped, the calibre's dash is a decimal point)."""
    parts = raw.split("_")
    if parts and parts[0].upper() in ("BULLET", "SHELL", "BOMB", "RKT", "ROCKET"):
        parts = parts[1:]
    text = " ".join(parts)
    out: list[str] = []
    for index, char in enumerate(text):
        is_decimal = (
            char == "-" and 0 < index < len(text) - 1 and text[index - 1].isdigit() and text[index + 1].isdigit()
        )
        out.append("." if is_decimal else char)
    return "".join(out) or raw


def ammo_table(sortie: PlayerSortie) -> AmmoTable:
    ammo = _dict(sortie.ammo)
    loaded, left, used = _dict(ammo.get("loaded")), _dict(ammo.get("left")), _dict(ammo.get("used"))
    has_end_record = ammo.get("left") is not None
    rows: list[AmmoRow] = []
    unknown: AmmoNote = ""
    for kind, label in AMMO_LABELS.items():
        if not (_int(loaded.get(kind)) or _int(left.get(kind))):
            continue
        is_unknown = _int(used.get(kind)) is None
        note: AmmoNote = ""
        if is_unknown:
            note = "resupplied" if sortie.resupplied else "no_end_record" if not has_end_record else "unreliable"
            unknown = unknown or note
        rows.append(
            AmmoRow(
                str(label),
                _count(loaded.get(kind)),
                _count(left.get(kind)) if has_end_record else display.DASH,
                display.DASH if is_unknown else _count(used.get(kind)),
                note,
            )
        )
    hits = [
        HitRow(ammo_name(_str(entry.get("ammo"))), _count(entry.get("hits_given")), _count(entry.get("hits_received")))
        for entry in (_dict(item) for item in _list(ammo.get("hits")))
        if _str(entry.get("ammo"))
    ]
    return AmmoTable(rows, hits, unknown)


# --- timeline -----------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TimelineRow:
    """One line of the timeline; a run of ground kills is one row with `children` (rendered folded).

    kind: the stored kind ('kill', 'takeoff', ...); tone: "good", "bad", "warn" or ''; clock: time since spawn;
    utc: UTC clock; place: tooltip with the position; text: detail sentence ('' when none); who: the counterpart;
    count: how many events a grouped row stands for (1 otherwise)."""

    kind: str
    icon: str
    label: str
    tone: str
    clock: str
    utc: str
    place: str = ""
    text: str = ""
    who: Who | None = None
    count: int = 1
    summary: str = ""
    children: Sequence["TimelineRow"] = field(default_factory=tuple)


def _timeline_text(kind: str, detail: str) -> str:
    if kind == "spawn":
        return str(SPAWN_LABELS.get(detail, detail))
    if kind in ("shot_down", "destroyed"):
        if detail == "taxi_accident":
            return _("Crashed while taxiing, before takeoff")
        if detail == "strafed":
            return _("Destroyed on the ground")
    if kind == "sortie_end":
        return display.badge_spec(display.OUTCOMES, detail)[0]
    return ""


def _timeline_row(sortie: PlayerSortie, entry: Json, lookup: Lookup) -> TimelineRow:
    kind = _str(entry.get("kind"))
    detail = _str(entry.get("detail"))
    at = _when(entry.get("at")) or sortie.spawned_at
    icon = EVENT_ICONS.get(kind, "")
    label = str(EVENT_LABELS.get(kind, kind.replace("_", " ").capitalize()))
    who = lookup.counterpart(entry.get("counterpart"))
    if kind in ("kill", "assist", "friendly_fire") and detail and (who is None or who.kind == "ai"):
        who = lookup.who(None, detail)  # an AI or ground victim (a friendly victim has no counterpart)
        if kind == "kill" and not _is_air_object(lookup, detail):
            icon, label = "event/kill-ground", str(_("Ground kill"))
    return TimelineRow(
        kind,
        icon,
        label,
        EVENT_TONES.get(kind, ""),
        since(sortie.spawned_at, at),
        utc_clock(at),
        _pos(entry.get("pos")),
        _timeline_text(kind, detail),
        who,
    )


def _group(rows: Sequence[TimelineRow]) -> TimelineRow:
    """A run of consecutive ground kills as one row: a count and the commonest victims, the single kills folded."""
    names: dict[str, int] = {}
    for row in rows:
        name = row.who.name if row.who is not None else ""
        names[name] = names.get(name, 0) + 1
    top = sorted(names.items(), key=lambda pair: (-pair[1], pair[0]))[:3]
    summary = ", ".join(f"{count}\N{MULTIPLICATION SIGN} {name}" for name, count in top)
    if len(names) > len(top):
        summary += _(", and more")
    first, last = rows[0], rows[-1]
    clock = first.clock if first.clock == last.clock else f"{first.clock} \N{EN DASH} {last.clock}"
    return TimelineRow(
        "kill_group",
        first.icon,
        _("Ground kills"),
        "good",
        clock,
        first.utc,
        count=len(rows),
        summary=summary,
        children=tuple(rows),
    )


def timeline_rows(sortie: PlayerSortie, lookup: Lookup) -> list[TimelineRow]:
    """The timeline in order, runs of `TIMELINE_GROUP_MIN` or more consecutive ground kills folded into one row."""
    rows = [_timeline_row(sortie, entry, lookup) for entry in timeline_entries(sortie)]
    out: list[TimelineRow] = []
    run: list[TimelineRow] = []

    def flush() -> None:
        if len(run) >= TIMELINE_GROUP_MIN:
            out.append(_group(run))
        else:
            out.extend(run)
        run.clear()

    for row in rows:
        if row.kind == "kill" and row.icon == "event/kill-ground":
            run.append(row)
        else:
            flush()
            out.append(row)
    flush()
    return out


# --- the page -----------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Detail:
    """Everything the detail template shows beyond the `PlayerSortie` itself (documented in detail.html)."""

    air_kills: Sequence[KillRow]
    assists: Sequence[KillRow]
    friendly: Sequence[KillRow]
    ground: GroundBreakdown
    shot_down_by: Sequence[KillerRow]
    damage: Sequence[DamageRow]
    damage_more: Sequence[DamageRow]
    ammo: AmmoTable
    timeline: Sequence[TimelineRow]
    timeline_more: Sequence[TimelineRow]
    timeline_events: int


def build_detail(sortie: PlayerSortie, made: Sequence[Kill], suffered: Sequence[Kill], lookup: Lookup) -> Detail:
    pvp_air, pvp_assists, friendly = pvp_kills(sortie, made, lookup)
    ai_air, ai_assists = ai_kill_rows(sortie, lookup)
    damage = damage_rows(sortie, lookup)
    timeline = timeline_rows(sortie, lookup)
    return Detail(
        air_kills=[*pvp_air, *ai_air],
        assists=[*pvp_assists, *ai_assists],
        friendly=friendly,
        ground=ground_breakdown(sortie),
        shot_down_by=shot_down_by(sortie, suffered, lookup),
        damage=damage[:DAMAGE_HEAD_ROWS],
        damage_more=damage[DAMAGE_HEAD_ROWS:],
        ammo=ammo_table(sortie),
        timeline=timeline[:TIMELINE_HEAD_ROWS],
        timeline_more=timeline[TIMELINE_HEAD_ROWS:],
        timeline_events=len(sortie.timeline),
    )
