"""A long-lived replay of the in-progress mission, for "online now" (FR-ING-12, TD-07).

The same `Replay` that ingest runs in batch is fed the lines DServer has written so far; `snapshot()` resolves them
provisionally, with the rules of the final pass (feed only records facts, doc 13 Structure). On top of that this module
follows who is connected (AType 20 and 21), because a player who sits in the lobby or has just ended a sortie is online
but has no open sortie. It is pure Python: reading files and writing the database are `ingest.live`'s job.

Limits, by nature of the log: a player who joined the server before this mission started has no AType 20 in it, so
they show up only once they spawn; and a player whose AType 21 never arrives stays listed until the mission ends.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from il2ks.core.catalog.loader import Catalog
from il2ks.core.logparse.events import (
    AccountUuid,
    LogEvent,
    PlayerConnectEvent,
    PlayerDisconnectEvent,
    PlayerSpawnEvent,
    ProfileUuid,
)
from il2ks.core.logparse.parser import ParseStats, parse_lines
from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.result import MissionInfo, SortieResult
from il2ks.core.replay.state import Replay

type LiveState = Literal["in_flight", "on_ground", "spawned", "connected"]
"""`in_flight`: sortie open and airborne. `on_ground`: open, landed. `spawned`: open, not taken off yet.
`connected`: online without an open sortie (lobby, between sorties)."""


@dataclass(frozen=True, slots=True)
class OnlinePlayer:
    account_uuid: AccountUuid
    profile_uuid: ProfileUuid
    name: str  # from the latest spawn; "" for a player who connected but never spawned in this mission
    coalition: int  # 0 = not known yet (never spawned)
    country: int  # 0 = not known yet
    aircraft_type: str  # log name of the open sortie's aircraft, "" when `state` is `connected`
    state: LiveState
    sortie_spawn_tick: int | None  # the open sortie's spawn, None when `connected`
    flight_time_s: float  # of the open sortie so far, 0 when `connected`
    kills_air: int  # this mission so far, over all the player's sorties
    kills_ground: int


@dataclass(frozen=True, slots=True)
class LiveView:
    """One provisional look at the running mission."""

    tick: int  # the last tick read: the mission's elapsed time, 50 ticks = 1 s
    mission: MissionInfo | None
    players: tuple[OnlinePlayer, ...]
    ended: bool  # AType 7 was seen: the mission is over, the server is about to load the next one


@dataclass(slots=True)
class _Presence:
    profile_uuid: ProfileUuid
    name: str = ""


def _state_of(sortie: SortieResult, is_open: bool) -> LiveState:
    if not is_open:
        return "connected"
    match sortie.outcome:
        case "in_flight":
            return "in_flight"
        case "landed":
            return "on_ground"
        case "not_taken_off":
            return "spawned"
        case _:
            return "connected"  # lost but its end isn't logged yet: nothing left to show


class LiveReplay:
    """Feed it lines as they appear; ask for a `LiveView` whenever one is wanted."""

    def __init__(self, catalog: Catalog, rules: ReplayRules | None = None) -> None:
        self._replay = Replay(catalog, rules)
        self._online: dict[AccountUuid, _Presence] = {}
        self.lines = 0  # parsed so far (blank lines excluded)
        self.bad_lines = 0

    def feed_lines(self, lines: Iterable[str]) -> None:
        stats = ParseStats()  # per call: its warning list would otherwise grow with every tick
        for event in parse_lines(lines, stats):
            self._feed(event)
        self.lines += stats.lines_total
        self.bad_lines += stats.lines_bad

    def _feed(self, event: LogEvent) -> None:
        self._replay.feed(event)
        match event:
            case PlayerConnectEvent():
                self._online.setdefault(event.account_uuid, _Presence(event.profile_uuid))
            case PlayerDisconnectEvent():
                self._online.pop(event.account_uuid, None)
            case PlayerSpawnEvent() if event.is_player:
                presence = self._online.setdefault(event.account_uuid, _Presence(event.profile_uuid))
                presence.name = event.name
                presence.profile_uuid = event.profile_uuid
            case _:
                pass

    def snapshot(self) -> LiveView:
        snap = self._replay.snapshot()
        by_account: defaultdict[AccountUuid, list[SortieResult]] = defaultdict(list)
        for sortie in snap.sorties:
            by_account[sortie.account_uuid].append(sortie)
        players: list[OnlinePlayer] = []
        for account, presence in self._online.items():
            sorties = by_account.get(account, [])
            pilots = [s for s in sorties if s.role == "pilot"] or sorties
            current = max(pilots, key=lambda s: s.spawn_tick, default=None)
            state: LiveState = "connected"
            if current is not None:
                state = _state_of(current, current.index in snap.open_sorties)
            flying = current is not None and state != "connected"
            players.append(
                OnlinePlayer(
                    account_uuid=account,
                    profile_uuid=presence.profile_uuid,
                    name=current.name if current is not None else presence.name,
                    coalition=current.coalition if current is not None else 0,
                    country=current.country if current is not None else 0,
                    aircraft_type=current.aircraft_type if current is not None and flying else "",
                    state=state,
                    sortie_spawn_tick=current.spawn_tick if current is not None and flying else None,
                    flight_time_s=current.flight_time_s if current is not None and flying else 0.0,
                    kills_air=sum(s.kills_air for s in sorties),
                    kills_ground=sum(s.kills_ground for s in sorties),
                )
            )
        ended = snap.mission is not None and snap.mission.completed_cleanly
        return LiveView(tick=snap.tick, mission=snap.mission, players=tuple(players), ended=ended)
