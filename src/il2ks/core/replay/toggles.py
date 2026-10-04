"""Rule toggles from the maintainer's old il2_stats mods (TD-16: configured, not patched, OQ-61). Section `[rules]`.

A changed toggle applies to older missions with `il2ks reprocess --all` (new missions use it as they are ingested).
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuleToggles:
    # Rams: two enemy aircraft destroyed together in a mid-air collision (`core.replay.rams`). True = each pilot is
    # credited a kill for the other aircraft (and the victim's loss is "shot down" instead of "crashed").
    # False = nobody is credited (the collision has no attacker in the log). Default True (OQ-89).
    credit_rams: bool = True
    # (A pilot killed under the parachute is always a death: the old `parachute_deaths` toggle was removed, OQ-99.)
    # Ram detection: both aircraft destroyed in the air within this many seconds and this distance of each other,
    # with nobody else credited and no gun hits between them (`core.replay.rams`). Tight on purpose (OQ-92).
    ram_window_s: float = 0.5
    ram_distance_m: float = 15.0
