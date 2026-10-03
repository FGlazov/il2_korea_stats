"""Replay rule thresholds. All are config values (FR-ING-14, FR-ING-17, FR-ING-21); defaults are the validated ones."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReplayRules:
    # Bailout rule v2 (FR-ING-14)
    bailout_min_distance_m: float = 100.0
    died_with_aircraft_s: float = 0.5
    disconnect_window_s: float = 30.0
    mission_end_window_s: float = 60.0
    # "mission_ended" outcome: AType 4 within this many seconds after AType 7 (doc 12)
    mission_end_sortie_window_s: float = 5.0
    # Disconnect = death only with damage in this window (FR-ING-21)
    disconnect_damage_window_s: float = 120.0
    # Structural failure definition v2 (FR-ING-17)
    structural_sudden_s: float = 1.0
    structural_fall_s: float = 1.0
    # Additive (iteration 1 replay, see design_doc/11_open_questions.md, section core.replay)
    # A sortie's aircraft destroyed after AType 4 still counts for the sortie within this window (the shot-down shape
    # logs AType 4 before AType 3, doc 12), or at any time if the pilot left an airborne aircraft (FR-ING-22).
    # The same window applies to the pilot bot's own AType 3. A larger value also turns a normally ended aircraft that
    # something destroys later into a loss and a death of that sortie (tests/unit/replay/test_post_end_window.py).
    post_end_destroy_window_s: float = 5.0
    # Damage-based credit: other damagers above this fraction of the victim get an assist (il2_stats used > 1%).
    assist_min_damage: float = 0.01
    # Resupply (FR-ING-24): a landing (AType 6) followed by another takeoff (AType 5) in the same sortie means the
    # aircraft may have been rearmed (no log event says so). True = treat it as resupplied, so ammo "used" is unknown.
    resupply_allowed: bool = True
