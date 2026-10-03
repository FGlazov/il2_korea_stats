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
