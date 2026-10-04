"""Log line -> typed event (FR-ING-3, TD-20).

Two steps per line:
1. a generic tokenizer splits `T:<tick> AType:<n> KEY:value KEY(...) (...) ...` into `{KEY: raw value}`;
2. a per-AType `_Spec` maps the tokens to the typed event from `events.py`. Tokens the spec doesn't map go to `extra`.

Values that can contain spaces or commas (AType 12 `TYPE`/`NAME`, AType 10 `NAME`/`TYPE`/`SKIN`, AType 0 `MFile`) are
"free text": their value runs up to the *next known key* of that AType, never up to a `[^,]`-style delimiter (TD-20).
Parenthesized values (`POS(...)`, `BC(...)`, `BP((x,z),...)`, the unlabelled `(x,y,z)` in AType 4/10) are kept with
their parentheses in the raw token, so `extra` stays verbatim.
"""

import re
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from il2ks.core.logparse.events import (
    AccountUuid,
    AirfieldEvent,
    AreaBoundaryEvent,
    BailoutEvent,
    BotRemovedEvent,
    DamageEvent,
    GenericEvent,
    GroupEvent,
    GunBurstEvent,
    HitEvent,
    InfluenceAreaEvent,
    KillEvent,
    LandingEvent,
    LogEvent,
    LogVersionEvent,
    MissionEndEvent,
    MissionObjectiveEvent,
    MissionStartEvent,
    ObjectId,
    ObjectSpawnEvent,
    PlayerConnectEvent,
    PlayerDisconnectEvent,
    PlayerSpawnEvent,
    Pos,
    ProfileUuid,
    RocketFiredEvent,
    RoundEndEvent,
    SortieEndEvent,
    StoreReleaseEvent,
    TakeoffEvent,
    WheelsOffEvent,
    WheelsOnEvent,
)

MAX_WARNINGS = 200
"""Global cap of detail warnings per mission (they're still counted in `lines_bad` / `warning_counts`). Warnings
for something new (see `_warn`) and the per-kind summaries are exempt, so this is "soft" by a handful."""

MAX_WARNINGS_PER_KIND = 20
"""At most this many detail warnings per `WarningKind` and mission, apart from the always-warned new ones."""

MAX_WARNING_LINE_CHARS = 200
"""A warning quotes at most this many characters of the offending line."""

UNLABELLED = "()"
"""Token key of the unlabelled `(x,y,z)` group (AType 4 and 10)."""

LOG_VERSION_ATYPE = 15

RAW_FIELD = "#raw"
"""`GenericEvent.fields` key holding the whole line remainder when an unknown AType's tokens can't be split."""

IGNORED_ATYPES = frozenset({27, 28})
"""Observed, documented ATypes we deliberately don't use (doc 12): `GenericEvent`, counted in `ignored_atypes`,
not in `unknown_atypes`. The never-seen 17, 22, 23, 29 do count as unknown: their appearance is news."""

type Extra = MappingProxyType[str, str]

_NO_EXTRA: Extra = MappingProxyType({})


class WarningKind(StrEnum):
    """What a parser warning is about. The value is the human-readable label used in warning texts and in
    `ParseStats.warning_counts` (FR-ING-11). The first six are the reasons a line is bad (`ParseError.kind`)."""

    MALFORMED_LINE = "malformed line"  # doesn't start with `T:<tick> AType:<n>`
    MALFORMED_TOKEN = "malformed token"  # stray text between or glued to tokens
    UNBALANCED_PARENTHESES = "unbalanced parentheses"
    DUPLICATE_KEY = "duplicate key"
    MISSING_KEY = "missing required key"
    BAD_VALUE = "bad value"  # a value that doesn't convert (not an integer, wrong coordinate count, ...)
    UNKNOWN_ATYPE = "unknown AType"  # not a bad line: a GenericEvent
    UNKNOWN_KEY = "unknown key"  # not a bad line: the token goes to `extra`
    VERSION_CHANGE = "log version change"  # not a bad line


class ParseError(ValueError):
    """A line that can't be turned into an event (malformed or truncated). `kind` says why (C2)."""

    def __init__(self, kind: WarningKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(slots=True)
class ParseStats:
    """Counters for one mission, recorded on `IngestRun` (FR-ING-11, TD-20)."""

    lines_total: int = 0
    lines_bad: int = 0
    log_version: int | None = None
    unknown_atypes: Counter[int] = field(default_factory=Counter[int])
    unknown_keys: Counter[str] = field(default_factory=Counter[str])  # "<atype>:<KEY>"
    warnings: list[str] = field(default_factory=list[str])
    ignored_atypes: Counter[int] = field(default_factory=Counter[int])  # IGNORED_ATYPES seen (27, 28)
    # Warnings by kind (WarningKind value): every occurrence, including the ones whose text was suppressed by the caps.
    warning_counts: Counter[str] = field(default_factory=Counter[str])
    warnings_emitted: Counter[str] = field(default_factory=Counter[str])  # per kind: texts actually in `warnings`
    warned_novelties: set[str] = field(default_factory=set[str])  # what already got its always-warned first text


# --- tokenizer ---------------------------------------------------------------------------------------------------

_HEAD_RE = re.compile(r"T:(\d+) +AType:(\d+)(?: +|$)")
_KEY_RE = re.compile(r"([A-Za-z][A-Za-z0-9_]*)([:(])")
_VALUE_RE = re.compile(r"\S*")

type _Terminators = Mapping[str, tuple[tuple[str, str], ...]]
"""Free-text key -> the later keys that can end its value, as (" KEY:", " KEY(") pairs, nearest key first."""

_NO_TERMINATORS: _Terminators = MappingProxyType({})


def _paren_end(text: str, open_at: int) -> int:
    """Index just past the `)` matching the `(` at `open_at`."""
    close = text.find(")", open_at + 1)
    if close < 0:
        raise ParseError(WarningKind.UNBALANCED_PARENTHESES, f"unclosed '(' at column {open_at}")
    if text.find("(", open_at + 1, close) < 0:
        return close + 1
    depth = 0
    for i in range(open_at, len(text)):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    raise ParseError(WarningKind.UNBALANCED_PARENTHESES, f"unclosed '(' at column {open_at}")


def _find_terminator(text: str, start: int, candidates: tuple[tuple[str, str], ...]) -> int:
    """End of a free-text value: where the nearest following known key starts (end of text if none is found)."""
    for colon_form, paren_form in candidates:
        at = text.find(colon_form, start)
        paren_at = text.find(paren_form, start)
        if paren_at >= 0 and (at < 0 or paren_at < at):
            at = paren_at
        if at >= 0:
            return at
    return len(text)


def tokenize(rest: str, terminators: _Terminators = _NO_TERMINATORS) -> dict[str, str]:
    """Split the part of a line after `AType:<n>` into `{KEY: raw value}` (TD-20).

    - `KEY:value`: the value runs to the next space. `KEY: value` (space after the colon, AType 0 `ROUNDS: 1`) takes
      the next word as the value unless that word is itself a key, so `MID: GType:2` gives `MID = ""`.
    - `KEY(...)` and the unlabelled `(...)` (key `UNLABELLED`): the value is the balanced group, parentheses included.
    - Free-text keys (in `terminators`): the value runs up to the nearest following known key, spaces and commas
      included.
    Raises `ParseError` on stray text, unbalanced parentheses and duplicate keys."""
    tokens: dict[str, str] = {}
    n = len(rest)
    pos = 0
    while True:
        while pos < n and rest[pos] == " ":
            pos += 1
        if pos >= n:
            return tokens
        if rest[pos] == "(":
            key = UNLABELLED
            end = _paren_end(rest, pos)
            value = rest[pos:end]
        else:
            m = _KEY_RE.match(rest, pos)
            if m is None:
                raise ParseError(
                    WarningKind.MALFORMED_TOKEN, f"unexpected text at column {pos}: {rest[pos : pos + 20]!r}"
                )
            key = m.group(1)
            start = m.end()
            if m.group(2) == "(":
                end = _paren_end(rest, start - 1)
                value = rest[start - 1 : end]
            elif key in terminators:
                end = _find_terminator(rest, start, terminators[key])
                value = rest[start:end]
            else:
                end = start
                if end < n and rest[end] != " ":
                    end = _match_end(_VALUE_RE, rest, end)
                else:
                    word = end
                    while word < n and rest[word] == " ":
                        word += 1
                    if word < n and rest[word] != "(" and _KEY_RE.match(rest, word) is None:
                        end = _match_end(_VALUE_RE, rest, word)
                value = rest[start:end].lstrip(" ")
        if key in tokens:
            raise ParseError(WarningKind.DUPLICATE_KEY, f"duplicate key {key!r}")
        tokens[key] = value
        pos = end
        if pos < n and rest[pos] != " ":
            raise ParseError(WarningKind.MALFORMED_TOKEN, f"unexpected text at column {pos}: {rest[pos : pos + 20]!r}")


def _match_end(pattern: re.Pattern[str], text: str, pos: int) -> int:
    m = pattern.match(text, pos)
    return pos if m is None else m.end()


# --- value conversion --------------------------------------------------------------------------------------------


class _Fields:
    """Typed access to one line's tokens. A missing or unconvertible value raises `ParseError`."""

    __slots__ = ("_tokens",)

    def __init__(self, tokens: dict[str, str]) -> None:
        self._tokens = tokens

    def raw(self, key: str) -> str:
        try:
            return self._tokens[key]
        except KeyError:
            raise ParseError(WarningKind.MISSING_KEY, f"missing key {key}") from None

    def integer(self, key: str) -> int:
        try:
            return int(self._tokens[key])
        except KeyError:
            raise ParseError(WarningKind.MISSING_KEY, f"missing key {key}") from None
        except ValueError:
            raise ParseError(WarningKind.BAD_VALUE, f"{key}: not an integer: {self._tokens[key]!r}") from None

    def oid(self, key: str) -> ObjectId:
        try:
            return ObjectId(int(self._tokens[key]))
        except KeyError:
            raise ParseError(WarningKind.MISSING_KEY, f"missing key {key}") from None
        except ValueError:
            raise ParseError(WarningKind.BAD_VALUE, f"{key}: not an integer: {self._tokens[key]!r}") from None

    def number(self, key: str) -> float:
        value = self.raw(key)
        try:
            return float(value)
        except ValueError:
            raise ParseError(WarningKind.BAD_VALUE, f"{key}: not a number: {value!r}") from None

    def flag(self, key: str) -> bool:
        value = self.raw(key)
        if value == "1":
            return True
        if value == "0":
            return False
        raise ParseError(WarningKind.BAD_VALUE, f"{key}: not 0 or 1: {value!r}")

    def _group(self, key: str) -> str:
        """Inside of a parenthesized value."""
        value = self.raw(key)
        if len(value) < 2 or value[0] != "(" or value[-1] != ")":
            raise ParseError(WarningKind.BAD_VALUE, f"{key}: expected '(...)': {value!r}")
        return value[1:-1]

    def pos(self, key: str = "POS") -> Pos:
        parts = self._group(key).split(",")
        if len(parts) != 3:
            raise ParseError(WarningKind.BAD_VALUE, f"{key}: expected 3 coordinates: {self.raw(key)!r}")
        try:
            return Pos(float(parts[0]), float(parts[1]), float(parts[2]))
        except ValueError:
            raise ParseError(WarningKind.BAD_VALUE, f"{key}: bad coordinates: {self.raw(key)!r}") from None

    def int_group(self, key: str) -> tuple[int, ...]:
        """`BC(0,0,1)` -> (0, 0, 1)."""
        inner = self._group(key)
        try:
            return tuple(int(v) for v in inner.split(",")) if inner else ()
        except ValueError:
            raise ParseError(WarningKind.BAD_VALUE, f"{key}: bad integer list: {self.raw(key)!r}") from None

    def id_list(self, key: str) -> tuple[ObjectId, ...]:
        """`IDS:1,2,3` -> (1, 2, 3); empty value -> ()."""
        value = self.raw(key)
        try:
            return tuple(ObjectId(int(v)) for v in value.split(",")) if value else ()
        except ValueError:
            raise ParseError(WarningKind.BAD_VALUE, f"{key}: bad ID list: {value!r}") from None

    def points_2d(self, key: str) -> tuple[tuple[float, float], ...]:
        """`BP((x,z),(x,z))` -> ((x, z), (x, z))."""
        inner = self._group(key)
        if not inner:
            return ()
        if inner[0] != "(" or inner[-1] != ")":
            raise ParseError(WarningKind.BAD_VALUE, f"{key}: expected '((x,z),...)': {self.raw(key)!r}")
        points: list[tuple[float, float]] = []
        for point in inner[1:-1].split("),("):
            coords = point.split(",")
            if len(coords) != 2:
                raise ParseError(WarningKind.BAD_VALUE, f"{key}: expected 2D points: {self.raw(key)!r}")
            try:
                points.append((float(coords[0]), float(coords[1])))
            except ValueError:
                raise ParseError(WarningKind.BAD_VALUE, f"{key}: bad point: {self.raw(key)!r}") from None
        return tuple(points)

    def countries(self, key: str) -> MappingProxyType[int, int]:
        """`CNTRS:0:0,501:1,601:2` -> {0: 0, 501: 1, 601: 2}."""
        value = self.raw(key)
        result: dict[int, int] = {}
        for pair in value.split(",") if value else ():
            country, sep, coalition = pair.partition(":")
            try:
                if not sep:
                    raise ValueError(pair)
                result[int(country)] = int(coalition)
            except ValueError:
                raise ParseError(WarningKind.BAD_VALUE, f"{key}: bad country:coalition list: {value!r}") from None
        return MappingProxyType(result)


# --- per-AType mapping -------------------------------------------------------------------------------------------

type _Builder = Callable[[int, _Fields, Extra], LogEvent]
type _Direct = Callable[[int, tuple[str, ...]], LogEvent]
"""Builds an event from the fast path's values (an absent optional key is None there). A value that does not convert
raises `ValueError`: `_parse` then falls back to `_Spec.build`."""


# --- fast path ---------------------------------------------------------------------------------------------------
# The generic tokenizer above walks every line token by token in Python. Almost all lines have exactly the shape
# their spec lists (keys in log order, single spaces), so each AType also gets one compiled regex that matches the
# whole remainder at once (the `re` engine is C), and `groups()` are the token values. A line that doesn't match
# *exactly* goes through `tokenize`, which stays the one place that defines the semantics, produces the errors (line
# column, offending text) and handles unknown and unusual lines. The fast path must give the tokens `tokenize` would;
# `tests/unit/logparse/test_fast_path.py` compares both on the real fixtures and on mutated lines. To remove the
# fast path: delete this section, `_Spec.fast`/`fast_line` and the `fast_line` use in `_parse`.

_PAREN_KEYS = frozenset({"POS", "BC"})
"""Keys written `KEY(...)` in the specs that get a fast path (no nested parentheses)."""

_PAREN_VALUE = r"(\([^()]*\))"


@dataclass(frozen=True, slots=True)
class _FastLine:
    """`pattern` matches a whole line remainder; group i is the value of `token_keys[i]` (None: optional key absent).
    `guards`: (group, (" NEXT:", " NEXT(")) for free-text values, which must not contain the terminator of their
    key, or `tokenize` would have ended them earlier."""

    pattern: re.Pattern[str]
    token_keys: tuple[str, ...]
    guards: tuple[tuple[int, str, str], ...]
    has_optional: bool


def _fast_line_for(keys: tuple[str, ...], free_text: frozenset[str], known_extra: tuple[str, ...]) -> _FastLine | None:
    pieces: list[str] = []
    token_keys: list[str] = []
    guards: list[tuple[int, str, str]] = []
    for i, key in enumerate(keys):
        if key == UNLABELLED:
            pieces.append(_PAREN_VALUE)
        elif key in _PAREN_KEYS:
            pieces.append(key + _PAREN_VALUE)
        elif key in free_text:
            following = keys[i + 1] if i + 1 < len(keys) else None
            if following is None:
                if known_extra:
                    return None  # the value could end at an optional key: leave it to `tokenize`
                pieces.append(f"{key}:(.*)")
            elif following == UNLABELLED:
                return None
            else:
                guards.append((len(token_keys), f" {following}:", f" {following}("))
                pieces.append(f"{key}:(.*?)")
        else:
            pieces.append(f"{key}:(\\S+)")
        token_keys.append(key)
    pattern = " ".join(pieces)
    for key in known_extra:
        pattern += f"(?: {key}:(\\S+))?"
        token_keys.append(key)
    pattern += r"\s*"
    return _FastLine(re.compile(pattern), tuple(token_keys), tuple(guards), bool(known_extra))


type _Values = tuple[str | None, ...]
"""The regex groups of a fast-path match, in `_FastLine.token_keys` order (None: an optional key that is absent)."""


def _guards_hold(fast: _FastLine, values: _Values) -> bool:
    """False when a free-text value contains the terminator of its key (then `tokenize` would have ended it earlier)."""
    for group, colon_form, paren_form in fast.guards:
        value = values[group]
        if colon_form in value or paren_form in value:  # pyright: ignore[reportOperatorIssue]
            return False
    return True


def _fast_tokens(fast: _FastLine, values: _Values) -> dict[str, str]:
    return {k: v for k, v in zip(fast.token_keys, values, strict=True) if v is not None}


@dataclass(frozen=True, slots=True)
class _Spec:
    """How one AType's tokens become an event.

    `keys`: the keys `build` reads, in log order. `known_extra`: keys we expect but don't map (they go to `extra`
    without being counted as unknown). `free_text`: keys whose value may contain spaces and commas."""

    keys: tuple[str, ...]
    build: _Builder
    free_text: frozenset[str] = frozenset()
    known_extra: tuple[str, ...] = ()
    fast: bool = True  # False: no fast path for this AType (see "fast path" below)
    direct: _Direct | None = None  # event straight from the fast path's values: the hot ATypes (see "fast path")
    key_set: frozenset[str] = field(init=False)
    terminators: _Terminators = field(init=False)
    fast_line: _FastLine | None = field(init=False)

    def __post_init__(self) -> None:
        order = [k for k in (*self.keys, *self.known_extra) if k != UNLABELLED]
        terminators = {
            key: tuple((f" {k}:", f" {k}(") for k in order[order.index(key) + 1 :]) for key in self.free_text
        }
        object.__setattr__(self, "key_set", frozenset(self.keys))
        object.__setattr__(self, "terminators", MappingProxyType(terminators))
        fast_line = _fast_line_for(self.keys, self.free_text, self.known_extra) if self.fast else None
        object.__setattr__(self, "fast_line", fast_line)


def _mission_start(t: int, f: _Fields, x: Extra) -> MissionStartEvent:
    return MissionStartEvent(
        tick=t,
        extra=x,
        game_date=f.raw("GDate"),
        game_time=f.raw("GTime"),
        mission_file=f.raw("MFile"),
        mission_id=f.raw("MID"),
        game_type=f.integer("GType"),
        countries=f.countries("CNTRS"),
        settings=f.raw("SETTS"),
        mods=f.integer("MODS"),
        preset=f.integer("PRESET"),
        aqm_id=f.integer("AQMID"),
    )


def _sortie_end(t: int, f: _Fields, x: Extra) -> SortieEndEvent:
    return SortieEndEvent(
        tick=t,
        extra=x,
        aircraft_id=f.oid("PLID"),
        bot_id=f.oid("PID"),
        bullets=f.integer("BUL"),
        shells=f.integer("SH"),
        bombs=f.integer("BOMB"),
        rockets=f.integer("RCT"),
        pos=f.pos(UNLABELLED),
    )


def _objective(t: int, f: _Fields, x: Extra) -> MissionObjectiveEvent:
    return MissionObjectiveEvent(
        tick=t,
        extra=x,
        object_id=f.oid("OBJID"),
        pos=f.pos(),
        coalition=f.integer("COAL"),
        objective_type=f.integer("TYPE"),
        result=f.integer("RES"),
        icon_type=f.integer("ICTYPE"),
    )


def _player_spawn(t: int, f: _Fields, x: Extra) -> PlayerSpawnEvent:
    return PlayerSpawnEvent(
        tick=t,
        extra=x,
        aircraft_id=f.oid("PLID"),
        bot_id=f.oid("PID"),
        bullets=f.integer("BUL"),
        shells=f.integer("SH"),
        bombs=f.integer("BOMB"),
        rockets=f.integer("RCT"),
        pos=f.pos(UNLABELLED),
        profile_uuid=ProfileUuid(f.raw("IDS")),
        account_uuid=AccountUuid(f.raw("LOGIN")),
        name=f.raw("NAME"),
        aircraft_type=f.raw("TYPE"),
        country=f.integer("COUNTRY"),
        form=f.integer("FORM"),
        airfield_id=f.oid("FIELD"),
        in_air=f.integer("INAIR"),
        parent_id=f.oid("PARENT"),
        is_player=f.flag("ISPL"),
        is_tstart=f.flag("ISTSTART"),
        payload_id=f.integer("PAYLOAD"),
        fuel=f.number("FUEL"),
        skin=f.raw("SKIN"),
        weapon_mods=f.integer("WM"),
    )


def _object_spawn(t: int, f: _Fields, x: Extra) -> ObjectSpawnEvent:
    return ObjectSpawnEvent(
        tick=t,
        extra=x,
        object_id=f.oid("ID"),
        object_type=f.raw("TYPE"),
        country=f.integer("COUNTRY"),
        name=f.raw("NAME"),
        parent_id=f.oid("PID"),
        pos=f.pos(),
    )


# --- direct builders (the hot ATypes) ----------------------------------------------------------------------------
# `_Spec.build` goes through a token dict and `_Fields`. For the ATypes that make up 98% of a log (hits, damage, kills,
# object declarations, gun bursts) the fast path's regex groups feed the event directly: same events, and a line with a
# bad value raises ValueError, which hands the line to `build` for the exact error. Keep them in step with the builders
# above; `tests/unit/logparse/test_fast_path.py` compares both on the real fixtures and on mutated lines.


def _pos_of(group: str) -> Pos:
    """`(x,y,z)` -> Pos. Raises ValueError (unpacking, float) when it is not three numbers."""
    x, y, z = group[1:-1].split(",")
    return Pos(float(x), float(y), float(z))


def _direct_hit(t: int, v: tuple[str, ...]) -> LogEvent:
    return HitEvent(tick=t, extra=_NO_EXTRA, ammo=v[0], attacker_id=ObjectId(int(v[1])), target_id=ObjectId(int(v[2])))


def _direct_damage(t: int, v: tuple[str, ...]) -> LogEvent:
    return DamageEvent(
        tick=t,
        extra=_NO_EXTRA,
        damage=float(v[0]),
        attacker_id=ObjectId(int(v[1])),
        target_id=ObjectId(int(v[2])),
        pos=_pos_of(v[3]),
    )


def _direct_kill(t: int, v: tuple[str, ...]) -> LogEvent:
    return KillEvent(
        tick=t, extra=_NO_EXTRA, attacker_id=ObjectId(int(v[0])), target_id=ObjectId(int(v[1])), pos=_pos_of(v[2])
    )


def _direct_object_spawn(t: int, v: tuple[str, ...]) -> LogEvent:
    mid: str | None = v[6]  # the optional `MID` (the one known extra key); None when absent
    return ObjectSpawnEvent(
        tick=t,
        extra=_NO_EXTRA if mid is None else MappingProxyType({"MID": mid}),  # pyright: ignore[reportUnnecessaryComparison]
        object_id=ObjectId(int(v[0])),
        object_type=v[1],
        country=int(v[2]),
        name=v[3],
        parent_id=ObjectId(int(v[4])),
        pos=_pos_of(v[5]),
    )


def _direct_gun_burst(t: int, v: tuple[str, ...]) -> LogEvent:
    return GunBurstEvent(tick=t, extra=_NO_EXTRA, object_id=ObjectId(int(v[0])), pos=_pos_of(v[1]))


_SPECS: Mapping[int, _Spec] = MappingProxyType(
    {
        0: _Spec(
            ("GDate", "GTime", "MFile", "MID", "GType", "CNTRS", "SETTS", "MODS", "PRESET", "AQMID"),
            _mission_start,
            free_text=frozenset({"MFile"}),
            known_extra=("ROUNDS", "POINTS"),
            fast=False,  # once per mission
        ),
        1: _Spec(
            ("AMMO", "AID", "TID"),
            lambda t, f, x: HitEvent(
                tick=t, extra=x, ammo=f.raw("AMMO"), attacker_id=f.oid("AID"), target_id=f.oid("TID")
            ),
            direct=_direct_hit,
        ),
        2: _Spec(
            ("DMG", "AID", "TID", "POS"),
            lambda t, f, x: DamageEvent(
                tick=t,
                extra=x,
                damage=f.number("DMG"),
                attacker_id=f.oid("AID"),
                target_id=f.oid("TID"),
                pos=f.pos(),
            ),
            direct=_direct_damage,
        ),
        3: _Spec(
            ("AID", "TID", "POS"),
            lambda t, f, x: KillEvent(tick=t, extra=x, attacker_id=f.oid("AID"), target_id=f.oid("TID"), pos=f.pos()),
            direct=_direct_kill,
        ),
        4: _Spec(("PLID", "PID", "BUL", "SH", "BOMB", "RCT", UNLABELLED), _sortie_end),
        5: _Spec(("PID", "POS"), lambda t, f, x: TakeoffEvent(tick=t, extra=x, object_id=f.oid("PID"), pos=f.pos())),
        6: _Spec(("PID", "POS"), lambda t, f, x: LandingEvent(tick=t, extra=x, object_id=f.oid("PID"), pos=f.pos())),
        7: _Spec((), lambda t, f, x: MissionEndEvent(tick=t, extra=x)),
        8: _Spec(
            ("OBJID", "POS", "COAL", "TYPE", "RES", "ICTYPE"),
            _objective,
            known_extra=("TARGETS", "OBJECTS", "PLANES", "MTARGETS", "MOBJETS"),
            fast=False,
        ),
        9: _Spec(
            ("AID", "COUNTRY", "POS"),
            lambda t, f, x: AirfieldEvent(
                tick=t, extra=x, airfield_id=f.oid("AID"), country=f.integer("COUNTRY"), pos=f.pos()
            ),
            known_extra=("IDS",),
            fast=False,
        ),
        10: _Spec(
            (
                "PLID",
                "PID",
                "BUL",
                "SH",
                "BOMB",
                "RCT",
                UNLABELLED,
                "IDS",
                "LOGIN",
                "NAME",
                "TYPE",
                "COUNTRY",
                "FORM",
                "FIELD",
                "INAIR",
                "PARENT",
                "ISPL",
                "ISTSTART",
                "PAYLOAD",
                "FUEL",
                "SKIN",
                "WM",
            ),
            _player_spawn,
            free_text=frozenset({"NAME", "TYPE", "SKIN"}),
        ),
        11: _Spec(
            ("GID", "IDS", "LID"),
            lambda t, f, x: GroupEvent(
                tick=t, extra=x, group_id=f.oid("GID"), member_ids=f.id_list("IDS"), leader_id=f.oid("LID")
            ),
        ),
        12: _Spec(
            ("ID", "TYPE", "COUNTRY", "NAME", "PID", "POS"),
            _object_spawn,
            free_text=frozenset({"TYPE", "NAME"}),
            known_extra=("MID",),
            direct=_direct_object_spawn,
        ),
        13: _Spec(
            ("AID", "COUNTRY", "ENABLED", "BC"),
            lambda t, f, x: InfluenceAreaEvent(
                tick=t,
                extra=x,
                area_id=f.oid("AID"),
                country=f.integer("COUNTRY"),
                enabled=f.flag("ENABLED"),
                bc=f.int_group("BC"),
            ),
        ),
        14: _Spec(
            ("AID", "BP"),
            lambda t, f, x: AreaBoundaryEvent(tick=t, extra=x, area_id=f.oid("AID"), points=f.points_2d("BP")),
            fast=False,  # nested parentheses
        ),
        15: _Spec(("VER",), lambda t, f, x: LogVersionEvent(tick=t, extra=x, version=f.integer("VER"))),
        16: _Spec(
            ("BOTID", "POS"), lambda t, f, x: BotRemovedEvent(tick=t, extra=x, bot_id=f.oid("BOTID"), pos=f.pos())
        ),
        18: _Spec(
            ("BOTID", "PARENTID", "POS"),
            lambda t, f, x: BailoutEvent(
                tick=t, extra=x, bot_id=f.oid("BOTID"), parent_id=f.oid("PARENTID"), pos=f.pos()
            ),
        ),
        19: _Spec((), lambda t, f, x: RoundEndEvent(tick=t, extra=x)),
        20: _Spec(
            ("USERID", "USERNICKID"),
            lambda t, f, x: PlayerConnectEvent(
                tick=t,
                extra=x,
                account_uuid=AccountUuid(f.raw("USERID")),
                profile_uuid=ProfileUuid(f.raw("USERNICKID")),
            ),
        ),
        21: _Spec(
            ("USERID", "USERNICKID"),
            lambda t, f, x: PlayerDisconnectEvent(
                tick=t,
                extra=x,
                account_uuid=AccountUuid(f.raw("USERID")),
                profile_uuid=ProfileUuid(f.raw("USERNICKID")),
            ),
        ),
        24: _Spec(
            ("OBJID", "POS"),
            lambda t, f, x: GunBurstEvent(tick=t, extra=x, object_id=f.oid("OBJID"), pos=f.pos()),
            direct=_direct_gun_burst,
        ),
        25: _Spec(
            ("OBJID", "POS", "TID"),
            lambda t, f, x: StoreReleaseEvent(
                tick=t, extra=x, object_id=f.oid("OBJID"), pos=f.pos(), store_id=f.oid("TID")
            ),
        ),
        26: _Spec(
            ("OBJID", "POS", "TID"),
            lambda t, f, x: RocketFiredEvent(
                tick=t, extra=x, object_id=f.oid("OBJID"), pos=f.pos(), rocket_id=f.oid("TID")
            ),
        ),
        30: _Spec(("ID", "POS"), lambda t, f, x: WheelsOffEvent(tick=t, extra=x, object_id=f.oid("ID"), pos=f.pos())),
        31: _Spec(("ID", "POS"), lambda t, f, x: WheelsOnEvent(tick=t, extra=x, object_id=f.oid("ID"), pos=f.pos())),
    }
)


# --- public API --------------------------------------------------------------------------------------------------


def _parse(line: str, *, fast: bool = True) -> tuple[int, LogEvent]:
    """Parse one line into (AType, event). `fast=False` skips the regex fast path (for the tests that compare)."""
    if fast:
        head = _HEAD_RE.match(line)
        if head is not None:
            atype = int(head.group(2))
            spec = _SPECS.get(atype)
            if spec is not None and spec.fast_line is not None:
                fast_line = spec.fast_line
                m = fast_line.pattern.fullmatch(line, head.end())
                values = None if m is None else m.groups()
                if values is not None and (not fast_line.guards or _guards_hold(fast_line, values)):
                    tick = int(head.group(1))
                    if spec.direct is not None:
                        try:
                            return atype, spec.direct(tick, values)
                        except ValueError:
                            pass  # a value doesn't convert: `build` below reports exactly what is wrong
                    tokens = _fast_tokens(fast_line, values)
                    extra = _NO_EXTRA if not fast_line.has_optional else _extra_of(tokens, spec.key_set)
                    return atype, spec.build(tick, _Fields(tokens), extra)
    text = line.strip()
    head = _HEAD_RE.match(text)
    if head is None:
        raise ParseError(WarningKind.MALFORMED_LINE, "line doesn't start with 'T:<tick> AType:<n>'")
    tick = int(head.group(1))
    atype = int(head.group(2))
    rest = text[head.end() :]
    spec = _SPECS.get(atype)
    if spec is None:
        try:
            fields = tokenize(rest)
        except ParseError:
            fields = {RAW_FIELD: rest}
        return atype, GenericEvent(tick=tick, atype=atype, fields=MappingProxyType(fields))
    tokens = tokenize(rest, spec.terminators)
    return atype, spec.build(tick, _Fields(tokens), _extra_of(tokens, spec.key_set))


def _extra_of(tokens: dict[str, str], key_set: frozenset[str]) -> Extra:
    """The tokens the spec doesn't map."""
    if len(tokens) == len(key_set) and all(k in key_set for k in tokens):
        return _NO_EXTRA
    return MappingProxyType({k: v for k, v in tokens.items() if k not in key_set})


def parse_line(line: str) -> LogEvent:
    """Parse one line. Raises `ParseError` on a malformed line. Unknown ATypes give a `GenericEvent`, never an error.

    An unknown AType whose tokens can't be split still gives a `GenericEvent`, with the raw remainder under
    `RAW_FIELD`."""
    return _parse(line)[1]


def _warn(stats: ParseStats, kind: WarningKind, text: str, novelty: str | None = None) -> None:
    """Record one warning of `kind`, subject to the caps (C2, FR-ING-11).

    Always kept: the first warning of a kind, or, when `novelty` is given, the first of each novelty (unknown keys:
    `"<atype>:<KEY>"`, unknown ATypes: `"<atype>"`). They are exempt from the caps because they are news. Everything
    else is kept while the kind has fewer than `MAX_WARNINGS_PER_KIND` texts and `warnings` has fewer than
    `MAX_WARNINGS`. Every occurrence is counted in `stats.warning_counts`; `parse_lines` adds a summary of the
    suppressed ones at the end."""
    stats.warning_counts[kind] += 1
    key = kind.value if novelty is None else f"{kind.value}:{novelty}"
    if key in stats.warned_novelties:
        if stats.warnings_emitted[kind] >= MAX_WARNINGS_PER_KIND or len(stats.warnings) >= MAX_WARNINGS:
            return
    else:
        stats.warned_novelties.add(key)
    stats.warnings_emitted[kind] += 1
    stats.warnings.append(text)


def _summarize_suppressed(stats: ParseStats) -> None:
    """One warning per kind that had suppressed texts, with how many (appended once at the end of a mission)."""
    for kind in WarningKind:
        suppressed = stats.warning_counts[kind] - stats.warnings_emitted[kind]
        if suppressed > 0:
            stats.warnings.append(f"{suppressed} more '{kind.value}' warnings suppressed")


def parse_lines(lines: Iterable[str], stats: ParseStats) -> Iterator[LogEvent]:
    """Parse lines lazily. Bad lines are counted and warned about in `stats`, never raised (FR-ING-3).

    Blank lines are skipped and not counted. `stats.log_version` is the first AType 15 `VER`; a different later
    version adds a warning. Warnings are capped per kind (see `_warn`); when the lines run out, one summary warning
    per kind says how many were suppressed."""
    for number, line in enumerate(lines, 1):
        if not line or line.isspace():
            continue
        stats.lines_total += 1
        try:
            atype, event = _parse(line)
        except ParseError as e:
            stats.lines_bad += 1
            _warn(stats, e.kind, f"line {number}: {e}: {line[:MAX_WARNING_LINE_CHARS]!r}")
            continue
        # `is`, not isinstance: the event classes are final, and this runs for every line
        cls = type(event)
        if cls is GenericEvent:
            if atype in IGNORED_ATYPES:
                stats.ignored_atypes[atype] += 1
            else:
                stats.unknown_atypes[atype] += 1
                _warn(
                    stats,
                    WarningKind.UNKNOWN_ATYPE,
                    f"line {number}: unknown AType {atype}: {line.strip()[:MAX_WARNING_LINE_CHARS]!r}",
                    novelty=str(atype),
                )
        elif event.extra:
            known_extra = _SPECS[atype].known_extra
            for key in event.extra:
                if key not in known_extra:
                    unknown = f"{atype}:{key}"
                    stats.unknown_keys[unknown] += 1
                    _warn(
                        stats,
                        WarningKind.UNKNOWN_KEY,
                        f"line {number}: unknown key {key} in AType {atype}: {line.strip()[:MAX_WARNING_LINE_CHARS]!r}",
                        novelty=unknown,
                    )
        if atype == LOG_VERSION_ATYPE and isinstance(event, LogVersionEvent):
            if stats.log_version is None:
                stats.log_version = event.version
            elif event.version != stats.log_version:
                _warn(
                    stats,
                    WarningKind.VERSION_CHANGE,
                    f"line {number}: log version changed from {stats.log_version} to {event.version}",
                )
        yield event
    _summarize_suppressed(stats)
