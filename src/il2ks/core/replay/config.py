"""Replay rule thresholds. All are config values (FR-ING-14, FR-ING-17, FR-ING-21); defaults are the validated ones."""

from dataclasses import dataclass, field

from il2ks.core.replay.toggles import RuleToggles


@dataclass(frozen=True, slots=True)
class ReplayRules:
    # Bailout rule v2 (FR-ING-14)
    bailout_min_distance_m: float = 100.0
    died_with_aircraft_s: float = 0.5
    disconnect_window_s: float = 30.0
    mission_end_window_s: float = 60.0
    # `ended_by_mission_end`: AType 4 within this many seconds after AType 7 (doc 12)
    mission_end_sortie_window_s: float = 5.0
    # Disconnect = death only with damage in this window (FR-ING-21)
    disconnect_damage_window_s: float = 120.0
    # Structural failure definition v2 (FR-ING-17)
    structural_sudden_s: float = 1.0
    structural_fall_s: float = 1.0
    # Additive (iteration 1 replay, see design_doc/13_game_rules.md)
    # A sortie's aircraft destroyed after AType 4 still counts for the sortie within this window (the shot-down shape
    # logs AType 4 before AType 3, doc 12), or at any time if the pilot left an airborne aircraft (FR-ING-22).
    # The same window applies to the pilot bot's own AType 3. Maintainer decision OQ-30: 5 minutes, to be safe. It only
    # applies to a sortie that ended airborne; see `post_end_destroy_window_ground_s` for the others.
    post_end_destroy_window_s: float = 300.0
    # The same window for a sortie whose aircraft was NOT airborne at the sortie end (landed and stopped, or never took
    # off): a pilot who landed, despawned and left must not die because the parked aircraft is destroyed minutes later.
    post_end_destroy_window_ground_s: float = 5.0
    # Damage-based credit: other damagers above this fraction of the victim get an assist (il2_stats used > 1%).
    assist_min_damage: float = 0.01
    # Resupply (FR-ING-24): a landing (AType 6) followed by another takeoff (AType 5) in the same sortie means the
    # aircraft may have been rearmed (no log event says so). True = treat it as resupplied, so ammo "used" is unknown.
    resupply_allowed: bool = True
    # A pilot's final position normally is the AType 16 position. Without one (AType 16 missing, or its position
    # garbage), the bot's latest AType 12 position counts if it was logged within this many seconds of the sortie end.
    pilot_pos_fallback_window_s: float = 5.0
    # Ammo left (AType 4) is only trustworthy when the sortie ended within this many seconds after the aircraft's
    # destruction (the pilot died with it). Later, the stores of the destroyed aircraft read as 0 (research, 210
    # samples), so "ammo used" is unknown for such a sortie.
    ammo_left_after_loss_s: float = 1.0
    # Time on target (FR-WEB-20, doc 13): a bomb, napalm or rocket release counts when an enemy ground object is within
    # this horizontal distance; each attack starts this many seconds before its first counted release; counted releases
    # further apart than the gap start a new attack.
    tot_target_radius_m: float = 3000.0
    tot_lead_in_s: float = 60.0
    tot_pass_gap_s: float = 300.0
    # Ammo attribution (FR-WEB-18, doc 02): a damage line takes the ammo of the attacker's hit on that target closest in
    # time, if one lies within this many seconds (either side; hits are logged a tick or two before the damage line).
    # Otherwise the damage stays "unattributed". Measured on the 210 samples (doc 13).
    ammo_window_s: float = 1.0
    # Labelling an explosion (doc 02 rule): a named ordnance or shell hit line of the same attacker within this many
    # seconds names it; else the lone ordnance of the loadout; else a release within `ordnance_release_window_s`.
    ordnance_hit_window_s: float = 1.0
    ordnance_release_window_s: float = 60.0
    # Timeline hits (FR-WEB-6, hits.py): damage lines of one attacker on one target form a burst while each is within
    # `hit_burst_gap_s` of the previous one (a burst lasts at most `hit_burst_max_s`); a burst under `hit_min_damage`
    # (a fraction of an object) is not shown.
    hit_burst_gap_s: float = 3.0
    hit_burst_max_s: float = 15.0
    hit_min_damage: float = 0.002
    # The `[rules]` toggles (OQ-61) travel with the rules, so every replay entry point sees them.
    toggles: RuleToggles = field(default_factory=RuleToggles)
