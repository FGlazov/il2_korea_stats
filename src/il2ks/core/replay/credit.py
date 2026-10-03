"""Damage-based kill and assist credit (TD-21, FR-ING-22).

Derived from il2_stats (MIT), see NOTICE: `Object.got_killed` / `got_damaged`. The most damage gets the kill, the others
get assists. An attacker named on the kill line (AID) is the killer unless it hit itself.
"""

from collections.abc import Iterator
from dataclasses import dataclass

from il2ks.core.replay.model import DamageRecord, HitRecord, Party, SortieState, TrackedObject, party_of


@dataclass(frozen=True, slots=True)
class Credited:
    party: Party
    is_killer: bool  # False = assist
    damage: float


def unit_objects(target: TrackedObject) -> Iterator[TrackedObject]:
    """The target and its crew/turrets: shooting the pilot counts as damage to the aircraft (il2_stats did that too)."""
    yield target
    yield from target.children


def is_self_attack(attacker: TrackedObject | None, victim: TrackedObject, victim_sortie: SortieState | None) -> bool:
    """Derived from il2_stats (MIT), see NOTICE: Object.is_attack_itself. The environment (None) isn't an attacker."""
    if attacker is None:
        return True
    if attacker.root is victim.root:
        return True
    return victim_sortie is not None and party_of(attacker) is victim_sortie


def damage_records(
    victim: TrackedObject, victim_sortie: SortieState | None, upto_tick: int, *, attackers_only: bool
) -> list[DamageRecord]:
    """Damage lines on the victim unit up to `upto_tick`. `attackers_only` drops environment and self damage."""
    records: list[DamageRecord] = []
    for obj in unit_objects(victim):
        for record in obj.damage_log:
            if record.tick > upto_tick:
                continue
            if attackers_only and is_self_attack(record.attacker, victim, victim_sortie):
                continue
            records.append(record)
    return records


def hit_records(
    victim: TrackedObject, victim_sortie: SortieState | None, upto_tick: int, *, attackers_only: bool
) -> list[HitRecord]:
    records: list[HitRecord] = []
    for obj in unit_objects(victim):
        for record in obj.hit_log:
            if record.tick > upto_tick:
                continue
            if attackers_only and is_self_attack(record.attacker, victim, victim_sortie):
                continue
            records.append(record)
    return records


def credit_kill(
    victim: TrackedObject,
    victim_sortie: SortieState | None,
    explicit_attacker: TrackedObject | None,
    upto_tick: int,
    assist_min_damage: float,
) -> list[Credited]:
    """Killer first, then assists by damage. Empty when nobody but the environment/self is responsible."""
    damage: dict[Party, float] = {}
    for record in damage_records(victim, victim_sortie, upto_tick, attackers_only=True):
        assert record.attacker is not None  # attackers_only
        party = party_of(record.attacker)
        damage[party] = damage.get(party, 0.0) + record.amount
    killer: Party | None = None
    if explicit_attacker is not None and not is_self_attack(explicit_attacker, victim, victim_sortie):
        killer = party_of(explicit_attacker)
    elif damage:
        killer = max(damage, key=lambda p: damage[p])  # first maximum in insertion order: deterministic
    if killer is None:
        return []
    credited = [Credited(killer, True, damage.get(killer, 0.0))]
    others = sorted(((p, d) for p, d in damage.items() if p is not killer), key=lambda pd: -pd[1])
    credited.extend(Credited(p, False, d) for p, d in others if d >= assist_min_damage)
    return credited
