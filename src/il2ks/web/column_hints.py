"""Short descriptions of table columns that are not self-explanatory (maintainer request 2026-10-04).

A table header shows its hint on hover, keyboard focus and tap (`{% sort_th %}` and `{% col_th %}`, see
`components/col_th.html`). The optional columns of `web.columns` carry their hint on the `Column`; every other header
names one of the keys below, e.g. `{% col_th _("Elo") hint="elo" %}`, so the wording of a term (Elo, time on target,
accuracy...) lives in one place. The rules behind the words are in design_doc/13_game_rules.md.
"""

from django.utils.translation import gettext_lazy as _

from il2ks.web.display import Label

# Translators (all column hints): shown as a tooltip when a visitor points at, focuses or taps a table column header.
# They explain what the number in that column means; keep them short and plain. The header itself is translated
# separately: reuse its wording.
HINTS: dict[str, Label] = {
    # Translators: tooltip of the "Elo" column header on the Elo leaderboards and an aircraft type's top pilots
    "elo": _(
        "Air-to-air skill rating, 1500 at the start. It rises when you shoot down another player and falls when "
        "another player shoots you down. Only air-superiority sorties count; jets and propeller aircraft are rated "
        "separately."
    ),
    # Translators: tooltip of the "Encounters" column header of the Elo leaderboards (the number of rated games)
    "elo_games": _(
        "Encounters: kills between two players in air-superiority sorties, counted for the killer and for the victim."
    ),
    # Translators: tooltip of the "Kills + losses" column header of an aircraft's matchup table
    "kills_plus_losses": _(
        "How often this aircraft and that enemy aircraft met: this aircraft's kills of it plus its losses to it."
    ),
    # Translators: tooltip of the "K/L" column header of an aircraft's matchup table
    "kl_matchup": _(
        'This aircraft\'s kills of that enemy aircraft divided by its losses to it. It says "no losses" if it was '
        "never lost to that aircraft, and shows a dash while kills plus losses are fewer than 10: too few for a "
        "reliable ratio."
    ),
    # Translators: tooltip of the "K/L" column header (kills per loss); a "loss" is a lost aircraft
    "kl": _(
        "Air kills divided by aircraft lost. Unlike K/D it also counts aircraft lost when the pilot survived, for "
        "example after a bailout."
    ),
    # Translators: tooltip of the "K/D" column header (kills per death)
    "kd": _("Air kills divided by deaths, a death being a sortie in which the pilot died. A dash with no deaths."),
    # Translators: tooltip of the "Survival" column header
    "survival": _("Share of the sorties in which the pilot did not die."),
    # Translators: tooltip of the "Air score" column header
    "score_air": _(
        "Points for air kills (a player is worth more than an AI aircraft) and assists, minus penalties for dying, "
        "being captured, losing the aircraft and friendly kills. Ground kills are scored separately."
    ),
    # Translators: tooltip of the "Ground score" column header
    "score_ground": _(
        "Points for ground kills (each kind of target has its own value, fences very little), with the same "
        "penalties as the air score. Air kills are scored separately."
    ),
    # Translators: tooltip of the "Score per hour" and "Ground score/h" column headers
    "score_per_hour": _(
        "Ground score earned in attack sorties per hour spent on target. Only pilots with enough attack sorties and "
        "time on target are ranked."
    ),
    # Translators: tooltip of the "Attack ground score" column header of the ground-score-per-hour leaderboard
    "score_attack": _("Ground score earned in attack sorties only."),
    # Translators: tooltip of the "Time on target" column header
    "time_on_target": _(
        "Time spent attacking enemy ground targets in attack sorties: from a minute before the first bomb or rocket "
        "released within 3 km of a target until the last such release. Releases far from any target count for "
        "nothing. Not the same as flight time."
    ),
    # Translators: tooltip of the "Flight time" column header
    "flight_time": _("Time in the air, from takeoff until landing or until the aircraft was lost."),
    # Translators: tooltip of the "Air superiority flight time" column header of the interception leaderboard
    "flight_time_air": _("Flight time in air-superiority sorties only."),
    # Translators: tooltip of the "Attack sorties" column header
    "attack_sorties": _("Sorties flown with bombs or rockets on board, or in a dedicated attacker such as the IL-10."),
    # Translators: tooltip of the "Air superiority sorties" column header
    "air_superiority_sorties": _("Sorties flown without bombs or rockets: guns only, with or without drop tanks."),
    # Translators: tooltip of the "Kills per hour" column header of the interception board and its home block
    "interception_rate": _(
        "Bombers, attackers and transports shot down per hour of flight in air-superiority sorties. Players flying "
        "with bombs or rockets count as well."
    ),
    # Translators: tooltip of the "Bombers and attackers shot down" column header of the interception leaderboard
    "interception_kills": _(
        "Credited kills of AI bombers, attackers and transports, and of players flying with bombs or rockets, made "
        "in air-superiority sorties."
    ),
    # Translators: tooltip of the "Tanks per hour" column header of the tank-busting board and its home block
    "tank_rate": _("Tanks destroyed in attack sorties per hour spent on target."),
    # Translators: tooltip of the "Tanks destroyed" column header of the tank-busting leaderboard
    "tanks": _("Tanks destroyed in attack sorties, moving or parked."),
    # Translators: tooltip of the "Assists" column header
    "assists": _(
        "Credit for damaging an aircraft or ground object that someone else then destroyed (at least 1% of its "
        "damage). Only assists on aircraft earn score points."
    ),
    # Translators: tooltip of the "Air assists" column header
    "assists_air": _("Assists on aircraft. They earn score points."),
    # Translators: tooltip of the "Ground assists" column header
    "assists_ground": _("Assists on ground objects. They are counted but earn no score points."),
    # Translators: tooltip of the "Friendly kills" column header
    "friendly_kills": _(
        "Aircraft or objects of your own side that you destroyed. They do not count as kills and cost score points."
    ),
    # Translators: tooltip of the "Planes lost" column header
    "planes_lost": _(
        "Aircraft destroyed or lost, including those where the pilot survived, for example by bailing out."
    ),
    # Translators: tooltip of the "Air kills (PvP)" column header
    "kills_air_pvp": _("Aircraft shot down that were flown by other players."),
    # Translators: tooltip of the "Air kills (AI)" column header
    "kills_air_ai": _("Aircraft shot down that were flown by the server's AI."),
    # Translators: tooltip of the "Air" column header of a mission sortie table and the last mission top pilots
    "kills_air_short": _("Aircraft shot down (air kills)."),
    # Translators: tooltip of the "Ground" column header of a mission sortie table and the last mission top pilots
    "kills_ground_short": _("Ground objects destroyed (ground kills): vehicles, tanks, ships, buildings and so on."),
    # Translators: tooltip of the "Damage taken" column header of a sortie list
    "damage_taken": _(
        "How much damage the aircraft had taken when the sortie ended or it was lost, as a share of its health: "
        "100% means it was destroyed."
    ),
    # Translators: tooltip of the "Role" column header of a mission's sortie table (the combat role of the sortie)
    "role": _(
        "Attack if the aircraft carried bombs or rockets (or is a dedicated attacker), otherwise air superiority."
    ),
    # Translators: tooltip of the "Outcome" column header (what happened to the aircraft)
    "outcome": _("What happened to the aircraft: landed, crashed, shot down and so on."),
    # Translators: tooltip of the "Pilot fate" column header (what happened to the pilot)
    "fate": _("What happened to the pilot: survived, killed or captured. The badge names the exact state."),
    # Translators: tooltip of the "Credit" column header of a table of kills (who gets the kill)
    "credit": _(
        "Kill: the attacker did the most damage. Assist: the attacker damaged it but someone else destroyed it. "
        "Friendly fire: both were on the same side."
    ),
    # Translators: tooltip of the "Damage dealt" column header of a sortie's damage table
    "damage_dealt": _(
        "Damage you did to this counterpart. Against players it is a percentage of their aircraft's health; against "
        "AI and ground objects it is the health units summed over all objects of that type."
    ),
    # Translators: tooltip of the "Hits dealt" column header of a sortie's damage table
    "hits_dealt": _("Number of hits you landed on this counterpart."),
    # Translators: tooltip of the "Damage taken" column header of a sortie's damage table
    "damage_taken_from": _("Damage this counterpart did to you, measured as in the column Damage dealt."),
    # Translators: tooltip of the "Hits taken" column header of a sortie's damage table
    "hits_taken": _("Number of hits this counterpart landed on you."),
    # Translators: tooltip of the "Damage" column header of a sortie's timeline
    "timeline_damage": _(
        "Damage of a significant hit, as a percentage of the object's health: plus if you dealt it, minus if you "
        "took it."
    ),
    # Translators: tooltip of the "Used" column header of a sortie's ammunition table
    "ammo_used": _("Rounds fired: loaded minus left. A dash when unknown, for example after a resupply."),
    # Translators: tooltip of the "Hits given" column header of a sortie's ammunition hit table
    "ammo_hits_given": _("Hits you scored with this ammunition."),
    # Translators: tooltip of the "Hits received" column header of a sortie's ammunition hit table
    "ammo_hits_received": _("Hits you took from this ammunition."),
    # Translators: tooltip of the "Released" column header of a sortie's ordnance table (bombs, rockets, napalm)
    "ord_released": _("How many of this ordnance the aircraft dropped or fired."),
    # Translators: tooltip of the "Detonations" column header of a sortie's ordnance table
    "ord_detonations": _("How many of the released pieces exploded."),
    # Translators: tooltip of the "Targets damaged" column header of a sortie's ordnance table
    "ord_targets": _("One detonation and one target that took damage."),
    # Translators: tooltip of the "Direct hits" column header of a sortie's ordnance table
    "ord_direct": _("Direct impacts logged with the ordnance named."),
    # Translators: tooltip of the "Instances" column header of an aircraft's "Hits to destroy" table
    "hits_instances": _("Counted kills of this aircraft in which this ammunition hit at least once."),
    # Translators: tooltip of the "Instances" column header of an aircraft's "Ammunition mixes" table
    "mix_instances": _("Counted kills of this aircraft in which exactly these ammunition types hit together."),
    # Translators: tooltip of the "Average hits" column header of an aircraft's "Hits to destroy" table
    "hits_average": _("Average number of gun hits that were needed to shoot this aircraft down."),
    # Translators: tooltip of the "Sorties" column header of a streak table
    "streak_sorties": _("Sorties in a row that the pilot survived."),
    # Translators: tooltip of the "Air kills" column header of a streak table
    "streak_kills": _("Air kills made during the streak."),
    # Translators: tooltip of the "Flight time" column header of a streak table
    "streak_time": _("Flight time added up over the streak's sorties."),
    # Translators: tooltip of the "Aircraft lost" column header of the profile's "Caused by" table
    "pve_lost": _("Sorties in which the aircraft was lost to this cause (the pilot may have survived)."),
    # Translators: tooltip of the optional "Elo (jet)" column header
    "elo_jet": _(
        "Air-to-air skill rating for jet aircraft, 1500 at the start, all time. It moves only when you and another "
        "player meet in air-superiority sorties."
    ),
    # Translators: tooltip of the optional "Elo (prop)" column header
    "elo_prop": _(
        "Air-to-air skill rating for propeller aircraft, 1500 at the start, all time. It moves only when you and "
        "another player meet in air-superiority sorties."
    ),
    # Translators: tooltip of the optional "Gun accuracy" column header of the player and aircraft lists
    "accuracy": _(
        "Gun hits per round fired, adding up only the sorties where the rounds fired are known. Bombs and rockets "
        "are not counted."
    ),
    # Translators: tooltip of the optional "Air accuracy" column header of the player and aircraft lists
    "accuracy_air": _(
        "Gun hits on aircraft per round fired, in air-superiority sorties where the rounds fired are known."
    ),
    # Translators: tooltip of the optional "Ground accuracy" column header of the player and aircraft lists
    "accuracy_ground": _(
        "Gun hits on ground targets per round fired, in attack sorties where the rounds fired are known."
    ),
    # Translators: tooltip of the optional "Air kills/h" column header of the aircraft list
    "kills_per_hour": _("Air kills per hour of flight time."),
    # Translators: tooltip of the optional "Bailouts" column header of the aircraft list
    "bailouts": _("Times a pilot jumped out of this aircraft."),
}
HINTS["ground_hour"] = HINTS["score_per_hour"]  # the optional "Ground score/h" column (its key is its sort key)


def hint_text(hint: object) -> str:
    """The text of a header's `hint`: a key of `HINTS`, or a ready (possibly lazy) text such as a `Column.hint`."""
    if isinstance(hint, str) and hint in HINTS:
        return str(HINTS[hint])
    return str(hint) if hint else ""
