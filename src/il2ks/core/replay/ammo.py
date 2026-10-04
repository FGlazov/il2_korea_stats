"""Ammo attribution (FR-WEB-18, doc 02 "Ammo attribution rule", doc 13 "Ammo and resupply").

Damage lines (AType 2) carry no ammo, hit lines (AType 1) do. This module attributes each damage line to an ammo type
and tallies, per pilot sortie, the damage per gun ammo and the ordnance use (released, detonations, targets damaged,
kills); and it lists the single-attacker aircraft kills with their gun hits, for "hits to destroy" per aircraft type.

**Closest-hit rule.** A damage line takes the ammo of the hit of the same attacker on the same target that is closest
in time, within `ReplayRules.ammo_window_s` (either side; the hit is logged a tick or two before the damage line). A tie
goes to the earlier hit, then to the named line over an explosion. Nothing in the window: "unattributed".

**Explosion hits** (97% of hit lines) are never counted as hits (TD-08). Replay keeps a player aircraft's explosion
lines as detonations (all lines of one aircraft on one tick, `model.Detonation`), only to name the ordnance. A
detonation is labelled, in this order (doc 02 rule; step 3 is split, see below):
1. a named ordnance or **shell** hit line of the same attacker within `ordnance_hit_window_s`: that ammo (a shell is a
   gun ammo, so the damage then counts for the gun);
2. the loadout (payload file) holds exactly one damaging ordnance type: it;
3. a store or rocket released within `ordnance_release_window_s` before it: that release's type, **if the release has
   one specific type**;
4. napalm is in the loadout and a store was released earlier in the sortie (lingering fire): napalm;
5. a release of one of several types of its class (`bombs_mixed`, `rockets_mixed`), else unattributed (never shown as
   "explosion").

Deviation from doc 02: a release that can't be typed (loadout with napalm and bombs: AType 25 doesn't say which) no
longer wins over napalm, because napalm fire is by far the most common source of explosions (on the samples, checked
against detonations that have a named ordnance line: 92% right against 67% with doc 02's order).

**What the log can't tell** (doc 12): AType 25/26 don't name the store, so a release is typed from the loadout. One
damaging type of that class (bomb/napalm/flare, or rocket) in the loadout: that type. Several: the generic
`bombs_mixed` / `rockets_mixed`. Drop tanks are released through the same AType 25: when the loadout also carries tanks
the number of bombs released is the bombs used (AType 10 minus AType 4, when trustworthy), else capped at the bombs
loaded. A rocket "release" is a salvo.
"""

from bisect import bisect_left
from collections.abc import Mapping
from dataclasses import dataclass, field
from itertools import islice
from typing import Literal

from il2ks.core.catalog.loader import (
    DAMAGING_KINDS,
    GENERIC_BOMBS,
    GENERIC_ORDNANCE,
    GENERIC_ROCKETS,
    Catalog,
    LoadoutItem,
    OrdnanceKind,
)
from il2ks.core.logparse.events import TICKS_PER_SECOND, ObjectId
from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.credit import damage_records, hit_records, is_self_attack
from il2ks.core.replay.fate import ticks, was_resupplied
from il2ks.core.replay.judge import Verdict
from il2ks.core.replay.model import (
    Detonation,
    HitRecord,
    MissionFacts,
    ReleaseClass,
    SortieState,
    TrackedObject,
    owner_sortie,
    party_of,
)
from il2ks.core.replay.result import (
    UNATTRIBUTED_ORDNANCE,
    AmmoHits,
    KillResult,
    OrdnanceUse,
    SingleAttackerKill,
    UnattributedDamage,
)

type AmmoKind = Literal["gun", "ordnance", "other"]
type Label = tuple[AmmoKind, str]
"""What a hit or detonation is: a gun ammo name, an ordnance key or another named ammo (flares)."""

AIRCRAFT_CLASSES = frozenset({"fighter", "attacker", "bomber", "transport"})
_SHELL_PREFIXES = ("SHELL_", "NPC_SHELL_")
_GUN_PREFIXES = ("BULLET_", *_SHELL_PREFIXES)


def is_gun_ammo(ammo: str) -> bool:
    """Bullets and shells (`BULLET_12-7_USA_API`, `SHELL_23_RUS_HET`, `NPC_SHELL_ENG_40_HE`), not ordnance or flares."""
    return ammo.startswith(_GUN_PREFIXES)


def _is_shell(ammo: str) -> bool:
    return ammo.startswith(_SHELL_PREFIXES)


@dataclass(slots=True)
class _Ordnance:
    released: int = 0
    detonations: int = 0
    targets_damaged: int = 0
    kills: int = 0
    dealt: float = 0.0
    taken: float = 0.0
    direct_hits: int = 0


@dataclass(slots=True)
class _SortieAmmo:
    gun_dealt: dict[str, float] = field(default_factory=dict[str, float])
    gun_taken: dict[str, float] = field(default_factory=dict[str, float])
    ordnance: dict[str, _Ordnance] = field(default_factory=dict[str, _Ordnance])
    unattributed_dealt: float = 0.0
    unattributed_taken: float = 0.0
    store_releases: int = 0  # AType 25 up to the sortie end: bombs, napalm, flares and drop tanks alike
    rocket_salvos: int = 0  # AType 26 up to the sortie end
    counted_units: set[tuple[int, int, str]] = field(default_factory=set[tuple[int, int, str]])

    def ord(self, key: str) -> _Ordnance:
        found = self.ordnance.get(key)
        if found is None:
            found = self.ordnance[key] = _Ordnance()
        return found


@dataclass(frozen=True, slots=True)
class AmmoAnalysis:
    """The result of `analyse`: per sortie index, and the single-attacker aircraft kills of the mission."""

    sorties: Mapping[int, "SortieAmmoResult"]
    single_attacker_kills: tuple[SingleAttackerKill, ...]


@dataclass(frozen=True, slots=True)
class SortieAmmoResult:
    gun_damage_dealt: Mapping[str, float]
    gun_damage_taken: Mapping[str, float]
    ordnance: tuple[OrdnanceUse, ...]
    unattributed: UnattributedDamage
    store_releases: int = 0
    rocket_salvos: int = 0


EMPTY_SORTIE_AMMO = SortieAmmoResult({}, {}, (), UnattributedDamage())


def merge_ammo_hits(hits: tuple[AmmoHits, ...], ammo: SortieAmmoResult) -> tuple[AmmoHits, ...]:
    """Add the attributed gun damage to the hit rows (a row is made for damage whose hit fell past the sortie end)."""
    rows = {h.ammo: h for h in hits}
    for name in sorted(set(ammo.gun_damage_dealt) | set(ammo.gun_damage_taken)):
        row = rows.get(name, AmmoHits(name))
        rows[name] = AmmoHits(
            name,
            row.hits_given,
            row.hits_received,
            ammo.gun_damage_dealt.get(name, 0.0),
            ammo.gun_damage_taken.get(name, 0.0),
        )
    return tuple(rows[name] for name in sorted(rows))


# --- loadout ---------------------------------------------------------------------------------------------------------

_STORE_KINDS: frozenset[OrdnanceKind] = frozenset({"bomb", "napalm", "flare", "tank"})


def _class_items(loadout: tuple[LoadoutItem, ...], release: ReleaseClass) -> list[LoadoutItem]:
    kinds: frozenset[OrdnanceKind] = _STORE_KINDS if release == "store" else frozenset({"rocket"})
    return [item for item in loadout if item.ordnance.kind in kinds]


def release_key(sortie: SortieState, release: ReleaseClass) -> str | None:
    """The ordnance a release of this class by this sortie was, or None for a drop tank and for nothing at all."""
    generic = GENERIC_BOMBS if release == "store" else GENERIC_ROCKETS
    loadout = sortie.loadout
    if loadout is None:  # payload unknown: the AType 10 counts say whether it carried anything of the class
        carried = sortie.ammo_loaded.bombs if release == "store" else sortie.ammo_loaded.rockets
        return generic if carried > 0 else None
    keys: list[str] = []
    for item in _class_items(loadout, release):
        if item.ordnance.kind != "tank" and item.ordnance.key not in keys:
            keys.append(item.ordnance.key)
    if not keys:
        return None
    return keys[0] if len(keys) == 1 else generic


def _damaging_types(sortie: SortieState) -> set[str]:
    return {i.ordnance.key for i in sortie.loadout or () if i.ordnance.kind in DAMAGING_KINDS}


def _has_napalm(sortie: SortieState) -> bool:
    return any(i.ordnance.kind == "napalm" for i in sortie.loadout or ())


def _has_tanks_with_stores(sortie: SortieState) -> bool:
    items = sortie.loadout or ()
    return any(i.ordnance.kind == "tank" for i in items) and any(
        i.ordnance.kind in ("bomb", "napalm", "flare") for i in items
    )


# --- the analysis ----------------------------------------------------------------------------------------------------


class _Analysis:
    def __init__(self, facts: MissionFacts, verdicts: list[Verdict], rules: ReplayRules) -> None:
        self.facts = facts
        self.catalog: Catalog = facts.catalog
        self.rules = rules
        self.window = round(rules.ammo_window_s * TICKS_PER_SECOND)
        self.hit_window = round(rules.ordnance_hit_window_s * TICKS_PER_SECOND)
        self.release_window = round(rules.ordnance_release_window_s * TICKS_PER_SECOND)
        self.verdicts = {s.index: v for s, v in zip(facts.sorties, verdicts, strict=True)}
        self.ends = {i: v.end_tick for i, v in self.verdicts.items()}
        self.cutoffs = {i: v.cutoff_tick for i, v in self.verdicts.items()}
        self.books = {s.index: _SortieAmmo() for s in facts.sorties}
        self._kinds: dict[str, Label] = {}
        self._det_labels: dict[int, Label | None] = {}
        self._damage_labels: dict[int, Label] = {}
        self._units: dict[int, tuple[TrackedObject, ...]] = {}
        self._unit_ids: dict[int, frozenset[ObjectId]] = {}

    # classification

    def classify(self, ammo: str) -> Label:
        found = self._kinds.get(ammo)
        if found is None:
            info = self.catalog.ordnance_for_ammo(ammo)
            found = ("ordnance", info.key) if info is not None else ("gun" if is_gun_ammo(ammo) else "other", ammo)
            self._kinds[ammo] = found
        return found

    # labelling an explosion

    def label_detonation(self, attacker: TrackedObject, det: Detonation) -> Label | None:
        if id(det) in self._det_labels:
            return self._det_labels[id(det)]
        label = self._label(attacker, det)
        self._det_labels[id(det)] = label
        return label

    def _label(self, attacker: TrackedObject, det: Detonation) -> Label | None:
        label = self._named_hit_near(attacker, det)
        if label is not None:
            return label
        sortie = owner_sortie(attacker)
        if sortie is None or sortie.role != "pilot":
            return None
        types = _damaging_types(sortie)
        if len(types) == 1:
            return ("ordnance", next(iter(types)))
        release = self._recent_release(attacker, sortie, det.tick)
        if release is not None and release[1] not in GENERIC_ORDNANCE:
            return release
        if _has_napalm(sortie) and any(t < det.tick for t, rel in attacker.store_releases if rel == "store"):
            return ("ordnance", "NAPALM")
        return release  # None, or a release of one of several types of the class (`bombs_mixed`, `rockets_mixed`)

    def _named_hit_near(self, attacker: TrackedObject, det: Detonation) -> Label | None:
        hits = attacker.given_hits
        start = bisect_left(hits, det.tick - self.hit_window, key=_hit_tick)
        best: tuple[tuple[int, int], Label] | None = None
        for hit in islice(hits, start, None):
            if hit.tick > det.tick + self.hit_window:
                break
            label = self.classify(hit.ammo)
            if label[0] == "other" or (label[0] == "gun" and not _is_shell(hit.ammo)):
                continue
            same_target = hit.target is not None and det.targets.get(hit.target.object_id) is hit.target
            rank = (abs(hit.tick - det.tick), 0 if same_target else 1)
            if best is None or rank < best[0]:
                best = (rank, label)
        return None if best is None else best[1]

    def _recent_release(self, attacker: TrackedObject, sortie: SortieState, tick: int) -> Label | None:
        for released_at, kind in reversed(attacker.store_releases):
            if released_at > tick:
                continue
            if tick - released_at > self.release_window:
                return None
            key = release_key(sortie, kind)
            if key is not None and self._damaging_key(key):
                return ("ordnance", key)
        return None

    def _damaging_key(self, key: str) -> bool:
        info = self.catalog.ordnance(key)
        return info is None or info.kind in DAMAGING_KINDS  # generic keys are bombs and rockets

    # the damage pass

    def _unit(self, target: TrackedObject) -> tuple[TrackedObject, ...]:
        """The target's root and its crew and turrets: hit lines name the aircraft, damage lines also its pilot."""
        root = target.root
        found = self._units.get(id(root))
        if found is None:
            found = self._units[id(root)] = (root, *_descendants(root))
        return found

    def closest(self, record_tick: int, target: TrackedObject, attacker: TrackedObject) -> tuple[Label, int] | None:
        """The label of the hit closest to a damage line and that hit's tick. None when no hit is in the window, or
        when the closest is an explosion no rule could name: either way the damage is unattributed.

        Hits on the target's whole unit count (the pilot bot's damage lines have no hit lines of their own)."""
        best: tuple[tuple[int, int, int], Label | None, int] | None = None
        unit = self._unit(target)
        unit_ids = self._unit_ids.get(id(unit[0]))
        if unit_ids is None:
            unit_ids = self._unit_ids[id(unit[0])] = frozenset(member.object_id for member in unit)
        low, high = record_tick - self.window, record_tick + self.window
        for member in unit:
            hits = member.hit_log
            if not hits:
                continue
            for hit in islice(hits, bisect_left(hits, low, key=_hit_tick), None):
                hit_tick = hit.tick
                if hit_tick > high:
                    break
                if hit.attacker is not attacker:
                    continue
                rank = (abs(hit_tick - record_tick), 1 if hit_tick > record_tick else 0, 0)
                if best is None or rank < best[0]:
                    best = (rank, self.classify(hit.ammo), hit_tick)
        dets = attacker.detonations
        if dets:
            for det in islice(dets, bisect_left(dets, low, key=_det_tick), None):
                det_tick = det.tick
                if det_tick > high:
                    break
                if det.targets.keys().isdisjoint(unit_ids):
                    continue
                rank = (abs(det_tick - record_tick), 1 if det_tick > record_tick else 0, 1)
                if best is None or rank < best[0]:
                    best = (rank, self.label_detonation(attacker, det), det_tick)
        return None if best is None or best[1] is None else (best[1], best[2])

    def run(self, kills: list[KillResult]) -> AmmoAnalysis:
        self._damage_pass()
        self._named_ordnance_hits()
        self._ordnance_totals(kills)
        return AmmoAnalysis(self._results(), single_attacker_kills(self.facts))

    def _damage_pass(self) -> None:
        for target in self.facts.objects:
            if not target.damage_log:
                continue
            target_party = party_of(target)
            for record in target.damage_log:
                attacker = record.attacker
                if attacker is None:
                    continue
                attacker_party = party_of(attacker)
                if attacker_party is target_party:
                    continue
                shooter = (
                    self.books[attacker_party.index]
                    if isinstance(attacker_party, SortieState) and record.tick <= self.ends[attacker_party.index]
                    else None
                )
                victim = (
                    self.books[target_party.index]
                    if isinstance(target_party, SortieState) and record.tick <= self.cutoffs[target_party.index]
                    else None
                )
                if shooter is None and victim is None:
                    continue
                found = self.closest(record.tick, target, attacker)
                if found is None:
                    if shooter is not None:
                        shooter.unattributed_dealt += record.amount
                    if victim is not None:
                        victim.unattributed_taken += record.amount
                    continue
                label, hit_tick = found
                self._damage_labels[id(record)] = label
                _tally(shooter, label, record.amount, dealt=True)
                _tally(victim, label, record.amount, dealt=False)
                if shooter is not None and label[0] == "ordnance":
                    unit = (id(target.root), hit_tick, label[1])
                    if unit not in shooter.counted_units:
                        shooter.counted_units.add(unit)
                        shooter.ord(label[1]).targets_damaged += 1

    def _named_ordnance_hits(self) -> None:
        """Releases, detonations and direct hits per pilot sortie (everything that isn't a damage line)."""
        for sortie in self.facts.sorties:
            if sortie.role != "pilot":
                continue
            book = self.books[sortie.index]
            end = self.ends[sortie.index]
            airframe = sortie.airframe
            for hit in airframe.given_hits:
                if hit.tick > end or hit.target is None or party_of(hit.target) is sortie:
                    continue
                label = self.classify(hit.ammo)
                if label[0] == "ordnance":
                    book.ord(label[1]).direct_hits += 1
            for det in airframe.detonations:
                if det.tick > end:
                    break
                label = self.label_detonation(airframe, det)
                key = label[1] if label is not None and label[0] == "ordnance" else UNATTRIBUTED_ORDNANCE
                if label is None or label[0] == "ordnance":
                    book.ord(key).detonations += 1
            self._count_releases(sortie, book, end)

    def _count_releases(self, sortie: SortieState, book: _SortieAmmo, end: int) -> None:
        stores = sum(1 for t, kind in sortie.airframe.store_releases if kind == "store" and t <= end)
        salvos = sum(1 for t, kind in sortie.airframe.store_releases if kind == "rocket" and t <= end)
        book.store_releases, book.rocket_salvos = stores, salvos
        if stores:
            key = release_key(sortie, "store")
            if key is not None:
                book.ord(key).released += self._bombs_released(sortie, stores)
        if salvos:
            key = release_key(sortie, "rocket")
            if key is not None:
                book.ord(key).released += salvos

    def _bombs_released(self, sortie: SortieState, stores: int) -> int:
        """AType 25 also logs drop tanks; with tanks and bombs on one aircraft, cap by what the ammo counts say."""
        if not _has_tanks_with_stores(sortie):
            return stores
        loaded = sortie.ammo_loaded.bombs
        left = sortie.ammo_left
        verdict = self.verdicts[sortie.index]
        if left is not None and left.bombs <= loaded and self._left_trusted(sortie, verdict):
            return min(stores, loaded - left.bombs)
        return min(stores, loaded)

    def _left_trusted(self, sortie: SortieState, verdict: Verdict) -> bool:
        """AType 4 describes the whole sortie: no resupply, and it wasn't logged long after the aircraft was lost."""
        airframe = sortie.airframe
        takeoffs = [t for t, _ in airframe.takeoffs if sortie.spawn_tick <= t <= verdict.active_end_tick]
        landings = [t for t, _ in airframe.landings if sortie.spawn_tick <= t <= verdict.active_end_tick]
        if was_resupplied(takeoffs, landings, self.rules):
            return False
        loss = verdict.loss
        return loss is None or verdict.end_tick - loss.tick <= ticks(self.rules.ammo_left_after_loss_s)

    def _ordnance_totals(self, kills: list[KillResult]) -> None:
        """Credited kills whose last damage line came from ordnance (damage labels from the damage pass)."""
        destroyed = {(o.object_id, o.destroyed_tick): o for o in self.facts.destroyed}
        by_index = {s.index: s for s in self.facts.sorties}
        for kill in kills:
            if kill.credit != "kill" or kill.is_friendly or kill.killer_sortie_index is None:
                continue
            victim = destroyed.get((kill.victim_object_id, kill.tick))
            killer = by_index[kill.killer_sortie_index]
            if victim is None:
                continue
            last = None
            for record in damage_records(victim, None, kill.tick, attackers_only=False):
                if record.attacker is not None and party_of(record.attacker) is killer:
                    last = record
            label = None if last is None else self._damage_labels.get(id(last))
            if label is not None and label[0] == "ordnance":
                self.books[killer.index].ord(label[1]).kills += 1

    def _results(self) -> dict[int, SortieAmmoResult]:
        out: dict[int, SortieAmmoResult] = {}
        for index, book in self.books.items():
            ordnance = tuple(
                OrdnanceUse(key, o.released, o.detonations, o.targets_damaged, o.kills, o.dealt, o.taken, o.direct_hits)
                for key, o in sorted(book.ordnance.items())
            )
            out[index] = SortieAmmoResult(
                book.gun_dealt,
                book.gun_taken,
                ordnance,
                UnattributedDamage(book.unattributed_dealt, book.unattributed_taken),
                book.store_releases,
                book.rocket_salvos,
            )
        return out


def _tally(book: _SortieAmmo | None, label: Label, amount: float, *, dealt: bool) -> None:
    if book is None:
        return
    kind, name = label
    if kind == "ordnance":
        entry = book.ord(name)
        if dealt:
            entry.dealt += amount
        else:
            entry.taken += amount
    else:
        target = book.gun_dealt if dealt else book.gun_taken
        target[name] = target.get(name, 0.0) + amount


def _descendants(obj: TrackedObject) -> list[TrackedObject]:
    found: list[TrackedObject] = []
    for child in obj.children:
        found.append(child)
        if child.children:
            found.extend(_descendants(child))
    return found


def _hit_tick(hit: HitRecord) -> int:
    return hit.tick


def _det_tick(det: Detonation) -> int:
    return det.tick


def single_attacker_kills(facts: MissionFacts) -> tuple[SingleAttackerKill, ...]:
    """Aircraft destroyed where every damage line on it came from one attacker (doc 02: hits to destroy), with that
    attacker's gun hits on it up to the destruction. No gun hits (a bomb on a parked aircraft, ramming): not listed.

    The environment or the aircraft itself among the damagers disqualifies, as in the old module. Crew and turrets count
    as the aircraft (`credit.damage_records`). Victim and attacker may be players or AI."""
    kills: list[SingleAttackerKill] = []
    for victim in facts.destroyed:
        if victim.info.cls not in AIRCRAFT_CLASSES or victim.is_bot or victim.destroyed_tick is None:
            continue
        records = damage_records(victim, victim.sortie, victim.destroyed_tick, attackers_only=False)
        attackers = {id(party_of(r.attacker)) for r in records if r.attacker is not None}
        if (
            not records
            or len(attackers) != 1
            or any(is_self_attack(r.attacker, victim, victim.sortie) for r in records)
        ):
            continue
        sole = next(r.attacker for r in records if r.attacker is not None)
        per_ammo: dict[str, int] = {}
        for hit in hit_records(victim, victim.sortie, victim.destroyed_tick, attackers_only=True):
            if hit.attacker is not None and party_of(hit.attacker) is party_of(sole) and is_gun_ammo(hit.ammo):
                per_ammo[hit.ammo] = per_ammo.get(hit.ammo, 0) + 1
        if per_ammo:
            kills.append(SingleAttackerKill(victim.object_type, tuple(sorted(per_ammo.items()))))
    return tuple(kills)


def analyse(facts: MissionFacts, verdicts: list[Verdict], kills: list[KillResult], rules: ReplayRules) -> AmmoAnalysis:
    """Gun damage per ammo, ordnance use and unattributed damage for every sortie, plus the single-attacker kills."""
    return _Analysis(facts, verdicts, rules).run(kills)


__all__ = [
    "EMPTY_SORTIE_AMMO",
    "AmmoAnalysis",
    "SortieAmmoResult",
    "analyse",
    "is_gun_ammo",
    "merge_ammo_hits",
    "release_key",
    "single_attacker_kills",
]
