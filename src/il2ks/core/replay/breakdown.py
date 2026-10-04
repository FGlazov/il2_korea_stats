"""Per-sortie breakdowns: damage exchanges per counterpart, hits per ammo type, friendly fire, and the key-event
timeline (TD-08)."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from il2ks.core.replay.fate import crew_death
from il2ks.core.replay.judge import Verdict
from il2ks.core.replay.model import MissionFacts, Party, SortieState, TrackedObject, is_gun_ammo, party_of
from il2ks.core.replay.result import AmmoHits, Counterpart, DamageExchange, KillResult, TimelineEntry


@dataclass(frozen=True, slots=True)
class GunHits:
    """Gun hits (bullets and shells; no ordnance, no explosions) a sortie gave, by what they landed on: an aircraft (or
    its crew and turrets) or anything else (vehicles, ships, statics, ...). Friendly targets count too."""

    air: int = 0
    ground: int = 0


type Breakdown = tuple[tuple[DamageExchange, ...], tuple[AmmoHits, ...], GunHits]


@dataclass(slots=True)
class _Exchange:
    counterpart: Counterpart
    dealt: float = 0.0
    taken: float = 0.0
    hits_dealt: int = 0
    hits_taken: int = 0


@dataclass(slots=True)
class _Breakdown:
    exchanges: dict[tuple[str, int], _Exchange] = field(default_factory=dict[tuple[str, int], _Exchange])
    given: dict[str, int] = field(default_factory=dict[str, int])
    received: dict[str, int] = field(default_factory=dict[str, int])
    gun_air: int = 0
    gun_ground: int = 0

    def exchange(self, party: Party) -> _Exchange:
        index = party.index if isinstance(party, SortieState) else None
        obj_type = party.aircraft_type if isinstance(party, SortieState) else party.object_type
        key = (obj_type, -1 if index is None else index)
        found = self.exchanges.get(key)
        if found is None:
            coalition = party.coalition
            found = self.exchanges[key] = _Exchange(Counterpart(obj_type, index, coalition))
        return found


def breakdowns(facts: MissionFacts, verdicts: list[Verdict]) -> dict[int, Breakdown]:
    """Damage and hits from each player sortie to its counterparts, and back. Self damage is left out."""
    ends = {s.index: v.end_tick for s, v in zip(facts.sorties, verdicts, strict=True)}
    cutoffs = {s.index: v.cutoff_tick for s, v in zip(facts.sorties, verdicts, strict=True)}
    per_sortie = {s.index: _Breakdown() for s in facts.sorties}
    for obj in facts.objects:
        target_party = party_of(obj)
        for record in obj.damage_log:
            if record.attacker is None:
                continue
            attacker_party = party_of(record.attacker)
            if attacker_party is target_party:
                continue
            if isinstance(attacker_party, SortieState) and record.tick <= ends[attacker_party.index]:
                per_sortie[attacker_party.index].exchange(target_party).dealt += record.amount
            if isinstance(target_party, SortieState) and record.tick <= cutoffs[target_party.index]:
                per_sortie[target_party.index].exchange(attacker_party).taken += record.amount
        for hit in obj.hit_log:
            if hit.attacker is None:
                continue
            attacker_party = party_of(hit.attacker)
            if attacker_party is target_party:
                continue
            if isinstance(attacker_party, SortieState) and hit.tick <= ends[attacker_party.index]:
                book = per_sortie[attacker_party.index]
                book.exchange(target_party).hits_dealt += 1
                book.given[hit.ammo] = book.given.get(hit.ammo, 0) + 1
                if is_gun_ammo(hit.ammo):
                    if obj.root.info.is_air:
                        book.gun_air += 1
                    else:
                        book.gun_ground += 1
            if isinstance(target_party, SortieState) and hit.tick <= cutoffs[target_party.index]:
                book = per_sortie[target_party.index]
                book.exchange(attacker_party).hits_taken += 1
                book.received[hit.ammo] = book.received.get(hit.ammo, 0) + 1
    out: dict[int, Breakdown] = {}
    for index, book in per_sortie.items():
        exchanges = tuple(
            DamageExchange(e.counterpart, e.dealt, e.taken, e.hits_dealt, e.hits_taken)
            for _, e in sorted(book.exchanges.items())
        )
        ammo = tuple(
            AmmoHits(a, book.given.get(a, 0), book.received.get(a, 0))
            for a in sorted(set(book.given) | set(book.received))
        )
        out[index] = (exchanges, ammo, GunHits(book.gun_air, book.gun_ground))
    return out


@dataclass(frozen=True, slots=True)
class FriendlyFire:
    """What one sortie did to its own side. Added to `SortieResult` as `friendly_*`."""

    kills: int = 0
    hits: int = 0
    damage: float = 0.0


def _friendly_shooter(
    attacker: TrackedObject | None, target: Party, tick: int, ends: dict[int, int]
) -> SortieState | None:
    """The player sortie behind a damage or hit line if it landed on a friendly object, else None. Friendly = same
    non-zero coalition, and not the sortie's own aircraft, crew or turrets (`party_of` folds those into the sortie)."""
    if attacker is None:
        return None
    shooter = party_of(attacker)
    if not isinstance(shooter, SortieState) or shooter is target or tick > ends[shooter.index]:
        return None
    if shooter.coalition == 0 or target.coalition != shooter.coalition:
        return None
    return shooter


def friendly_fire(facts: MissionFacts, verdicts: list[Verdict], kills: list[KillResult]) -> dict[int, FriendlyFire]:
    """Friendly kills, hits and damage per player sortie (index -> totals), apart from the normal counters.

    Hits and damage count up to the shooter's end tick like `breakdowns` does for hits and damage dealt."""
    ends = {s.index: v.end_tick for s, v in zip(facts.sorties, verdicts, strict=True)}
    n_kills = {i: 0 for i in ends}
    n_hits = {i: 0 for i in ends}
    damage = {i: 0.0 for i in ends}
    for k in kills:
        if k.is_friendly and k.credit == "kill" and k.killer_sortie_index in n_kills:
            n_kills[k.killer_sortie_index] += 1
    for obj in facts.objects:
        target = party_of(obj)
        for record in obj.damage_log:
            shooter = _friendly_shooter(record.attacker, target, record.tick, ends)
            if shooter is not None:
                damage[shooter.index] += record.amount
        for hit in obj.hit_log:
            shooter = _friendly_shooter(hit.attacker, target, hit.tick, ends)
            if shooter is not None:
                n_hits[shooter.index] += 1
    return {i: FriendlyFire(n_kills[i], n_hits[i], damage[i]) for i in ends}


def counterpart_of(party: Party) -> Counterpart:
    if isinstance(party, SortieState):
        return Counterpart(party.aircraft_type, party.index, party.coalition)
    return Counterpart(party.object_type, None, party.coalition)


def timeline(
    sortie: SortieState, verdict: Verdict, kills: list[KillResult], hits: Sequence[TimelineEntry] = ()
) -> tuple[TimelineEntry, ...]:
    """Key events with positions (no flight track, TD-08). Ordered by tick, then by insertion."""
    airframe = sortie.airframe
    # The hits come right after the spawn, so on one tick a hit sorts before the kill or loss it led to.
    entries: list[TimelineEntry] = [TimelineEntry(sortie.spawn_tick, "spawn", sortie.spawn_type, sortie.spawn_pos)]
    entries += hits
    end = verdict.end_tick
    active = verdict.active_end_tick
    entries += [TimelineEntry(t, "takeoff", pos=p) for t, p in airframe.takeoffs if sortie.spawn_tick <= t <= active]
    entries += [TimelineEntry(t, "landing", pos=p) for t, p in airframe.landings if sortie.spawn_tick <= t <= active]
    for kill in kills:
        if kill.killer_sortie_index == sortie.index and not kill.is_friendly:
            entries.append(
                TimelineEntry(
                    kill.tick,
                    "kill" if kill.credit == "kill" else "assist",
                    kill.victim_type,
                    kill.pos,
                    Counterpart(kill.victim_type, kill.victim_sortie_index, kill.victim_coalition),
                )
            )
        elif kill.killer_sortie_index == sortie.index:
            entries.append(TimelineEntry(kill.tick, "friendly_fire", kill.victim_type, kill.pos))
    loss = verdict.loss
    killer = next((k for k in kills if k.victim_sortie_index == sortie.index and k.credit == "kill"), None)
    if killer is not None and killer.killer_type is not None:
        counterpart = Counterpart(killer.killer_type, killer.killer_sortie_index, killer.killer_coalition)
    elif verdict.killer is not None:  # no KillResult names this sortie as victim (a gunner's), so use the verdict's
        counterpart = counterpart_of(verdict.killer)
    else:
        counterpart = None
    if loss is not None:
        kind = "shot_down" if verdict.loss_cause == "attacker" else "destroyed"
        detail = "taxi_accident" if verdict.taxi_accident else "strafed" if verdict.strafed_on_ground else ""
        entries.append(TimelineEntry(loss.tick, kind, detail, loss.pos, counterpart))
    elif verdict.died_tick is not None:  # the crew member died but the aircraft wasn't lost
        crew = crew_death(sortie)
        kind = "killed" if verdict.loss_cause == "attacker" else "died"
        pos = crew.destroyed_pos if crew is not None else None
        entries.append(TimelineEntry(verdict.died_tick, kind, pos=pos, counterpart=counterpart))
    if verdict.fate == "bailed_out":
        bot = sortie.bot
        tick = bot.bailout_tick if bot.bailout_tick is not None else bot.removed_tick
        entries.append(
            TimelineEntry(tick if tick is not None else end, "bailout", pos=bot.removed_pos or bot.bailout_pos)
        )
    if verdict.disconnect_tick is not None or verdict.fate == "disconnected":
        entries.append(
            TimelineEntry(
                verdict.disconnect_tick if verdict.disconnect_tick is not None else end, "disconnect", pos=airframe.pos
            )
        )
    end_pos = sortie.end_pos or sortie.bot.removed_pos
    entries.append(
        TimelineEntry(end, "sortie_end", "mission_end" if verdict.ended_by_mission_end else verdict.outcome, end_pos)
    )
    return tuple(sorted(entries, key=lambda e: e.tick))  # sorted() is stable


__all__ = ["FriendlyFire", "breakdowns", "counterpart_of", "friendly_fire", "timeline"]
