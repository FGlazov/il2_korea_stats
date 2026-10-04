"""Rule toggles from the maintainer's old il2_stats mods (TD-16: configured, not patched). Config section `[rules]`.

Changing a toggle takes effect with `il2ks reprocess --all`. WIP: the toggles are loaded and `detect_rams` finds the
signal; wiring them into kill credit and the death rules is not done yet (see the iteration 2 report).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from il2ks.core.logparse.events import TICKS_PER_SECOND
from il2ks.core.replay.model import TrackedObject, distance


@dataclass(frozen=True, slots=True)
class RuleToggles:
    # Rams: two aircraft destroyed together in a collision. True = the survivor-less pair credits each other a kill.
    # Default False = today's behaviour (a ram has no damage attacker, so nobody is credited).
    credit_rams: bool = False
    # Parachute deaths: a pilot killed after leaving the aircraft. False = the "no parachute deaths" mod (not a death).
    # Default True = today's behaviour (the death counts).
    parachute_deaths: bool = True
    # Ram detection: both aircraft destroyed in the air within this many seconds and this distance of each other,
    # and neither was hit by the other's guns (research, 210 samples: about 20 candidates).
    ram_window_s: float = 2.0
    ram_distance_m: float = 50.0


@dataclass(frozen=True, slots=True)
class Ram:
    first: TrackedObject
    second: TrackedObject
    tick: int


def detect_rams(destroyed: Sequence[TrackedObject], toggles: RuleToggles) -> list[Ram]:
    """Pairs of aircraft destroyed in the air close in time and space with no gun hits between them."""
    air = [
        o for o in destroyed if o.info.is_air and not o.is_bot and o.destroyed_airborne and o.destroyed_tick is not None
    ]
    window = round(toggles.ram_window_s * TICKS_PER_SECOND)
    rams: list[Ram] = []
    for i, a in enumerate(air):
        for b in air[i + 1 :]:
            if (
                a.destroyed_tick is None
                or b.destroyed_tick is None
                or a.destroyed_pos is None
                or b.destroyed_pos is None
            ):
                continue
            if abs(b.destroyed_tick - a.destroyed_tick) > window:
                continue
            if distance(a.destroyed_pos, b.destroyed_pos) > toggles.ram_distance_m:
                continue
            if any(h.attacker is b for h in a.hit_log) or any(h.attacker is a for h in b.hit_log):
                continue
            rams.append(Ram(a, b, min(a.destroyed_tick, b.destroyed_tick)))
    return rams
