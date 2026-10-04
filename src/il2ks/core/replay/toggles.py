"""Rule toggles from the maintainer's old il2_stats mods (TD-16: configured, not patched, OQ-61). Section `[rules]`.

A changed toggle applies to older missions with `il2ks reprocess --all` (new missions use it as they are ingested).
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuleToggles:
    # Rams: two enemy aircraft destroyed together in a mid-air collision (`core.replay.rams`). True = each pilot is
    # credited a kill for the other aircraft (and the victim's loss is "shot down" instead of "crashed").
    # Default False = nobody is credited (the collision has no attacker in the log).
    credit_rams: bool = False
    # Parachute deaths: a pilot killed after leaving the aircraft in the air (a detected bailout). True = it is a death,
    # as before. False = the "no parachute deaths" mod: the bailout is a lost aircraft but the pilot survives, and the
    # shooter gets no kill for the pilot.
    parachute_deaths: bool = True
    # Ram detection: both aircraft destroyed in the air within this many seconds and this distance of each other,
    # with nobody else credited and no gun hits between them (`core.replay.rams`).
    ram_window_s: float = 2.0
    ram_distance_m: float = 50.0
