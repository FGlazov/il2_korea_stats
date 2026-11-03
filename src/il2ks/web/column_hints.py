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
    # Translators: tooltip of the "Elo" column header of the aircraft list (the rating of an aircraft type, not a pilot)
    "elo_aircraft": _(
        "Rating of the aircraft type from air superiority duels between types. Every kill between two players in "
        "air superiority sorties is a game won by the killer's type, whoever flew it; 1500 at the start of a tour. "
        "A jet type beating a propeller type, and kills between two aircraft of one type, do not count. All time "
        "counts the best tour with enough games."
    ),
    # Translators: tooltip of the "Elo" column header on the Elo leaderboards and an aircraft type's top pilots
    "elo": _(
        "Air-to-air skill rating, 1500 at the start. It rises when you shoot down another player and falls when "
        "another player shoots you down. Only air superiority sorties count; jets and propeller aircraft are rated "
        "separately."
    ),
    # Translators: tooltip of the "Encounters" column header of the Elo leaderboards (the number of rated games)
    "elo_games": _(
        "Encounters: kills between two players in air superiority sorties, counted for the killer and for the "
        "victim, except a jet shooting down a propeller aircraft."
    ),
    # Translators: tooltip of the "Kills + losses" column header of an aircraft's matchup table
    "kills_plus_losses": _(
        "How often this aircraft and that enemy aircraft met: this aircraft's kills of it plus its losses to it."
    ),
    # Translators: tooltip of the "K/L" column header of an aircraft's matchup table
    "kl_matchup": _(
        "This aircraft's kills of that enemy aircraft divided by its losses to it. It says “no losses” if it was "
        "never lost to that aircraft, and shows a dash while kills plus losses are fewer than 10: too few for a "
        "reliable ratio."
    ),
    # Translators: tooltip of the "K/L" column header (kills per loss); a "loss" is a lost aircraft
    "kl": _(
        "Air kills divided by aircraft lost. Unlike K/D it also counts aircraft lost when the pilot survived, for "
        "example after a bailout."
    ),
    # Translators: tooltip of the "K/D" column header (kills per death)
    "kd": _(
        "Air kills divided by deaths, a death being a sortie in which the pilot died. A dash when there are no deaths."
    ),
    # Translators: tooltip of the "Longest kill streak" column header of the player list (air ironman run)
    "streak_kills_air": _(
        "The most air kills in one run of air sorties without a death or capture. Losing a ground attacker does not "
        "end it. Every tour starts afresh; this is the best tour."
    ),
    # Translators: tooltip of the "Longest ground kill streak" column header of the player list (ground ironman run)
    "streak_kills_ground": _(
        "The most ground kills in one run of attack sorties without a death or capture. Losing a fighter does not end "
        "it. Every tour starts afresh; this is the best tour."
    ),
    # Translators: tooltip of the "Survival" column header
    "survival": _("Share of the sorties in which the pilot did not die."),
    # Translators: tooltip of the "Air score" column header
    "score_air": _(
        "Points for air kills (a player is worth more than an AI aircraft) and assists, plus points for flight time "
        "if the server enables them, minus penalties for dying, being captured, losing the aircraft and friendly "
        "kills. Ground kills are scored separately."
    ),
    # Translators: tooltip of the "Ground score" column header
    "score_ground": _(
        "Points for ground kills (each kind of target has its own value, fences very little), with the same "
        "penalties as the air score. Air kills are scored separately."
    ),
    # Translators: tooltip of the "Attack proficiency" column header of its leaderboard and home block
    "score_per_hour": _(
        "Ground score earned in attack sorties per hour spent on target. Only pilots with enough attack sorties and "
        "time on target are ranked."
    ),
    # Translators: tooltip of the "Time on target" column header
    "time_on_target": _(
        "Time spent attacking enemy ground targets in attack sorties: from a minute before the first bomb or rocket "
        "released within 3 km of a target until a minute after the last such release. Releases far from any target "
        "count for nothing. Not the same as flight time."
    ),
    # Translators: tooltip of the "Attack sorties" column header
    "attack_sorties": _("Sorties flown with bombs or rockets on board, or in a dedicated attacker such as the IL-10."),
    # Translators: tooltip of the optional "Share of attack sorties" column header of the aircraft list
    "attack_share": _(
        "Share of the sorties flown with bombs or rockets on board, or in a dedicated attacker such as the IL-10."
    ),
    # Translators: tooltip of the "Air superiority sorties" column header
    "air_superiority_sorties": _("Sorties flown without bombs or rockets: guns only, with or without drop tanks."),
    # Translators: tooltip of the "Kills per hour" column header of the interception board and its home block
    "interception_rate": _(
        "Bombers, attackers and transports shot down per hour of flight in air superiority sorties. Players flying "
        "with bombs or rockets count as well."
    ),
    # Translators: tooltip of the "Bombers and attackers shot down" column header of the interception leaderboard
    "interception_kills": _(
        "Credited kills of AI bombers, attackers and transports, and of players flying with bombs or rockets, made "
        "in air superiority sorties."
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
    # Translators: tooltip of the "Friendly-fire air kills" column header
    "friendly_kills_air": _(
        "Aircraft of the shooter's own side that were shot down. They do not count as kills and cost score points."
    ),
    # Translators: tooltip of the "Friendly-fire ground kills" column header
    "friendly_kills_ground": _(
        "Vehicles, guns, buildings and other ground objects of the shooter's own side that were destroyed. "
        "They do not count as kills and cost score points."
    ),
    # Translators: tooltip of the "Taxi/takeoff accidents" column header
    "taxi_accidents": _("Aircraft lost by the pilot's own doing, before takeoff or within a minute after it."),
    # Translators: tooltip of the "Botched landings" column header
    "botched_landings": _("Aircraft crashed or badly damaged landing at an airfield, by the pilot's own doing."),
    # Translators: tooltip of the "Strafed on the ground" column header
    "strafed_on_ground": _("Aircraft destroyed by the enemy while on the ground."),
    # Translators: tooltip of the "Aircraft lost" column header
    "planes_lost": _(
        "Aircraft destroyed or lost, including those where the pilot survived, for example by bailing out."
    ),
    # Translators: tooltip of the "Damage taken" column header of a sortie list
    "damage_taken": _(
        "How much damage the aircraft carried when the sortie ended or it was lost, as a share of its health: "
        "100% means it was destroyed. A landing followed by a new takeoff repairs the aircraft, so only the last "
        "flight counts."
    ),
    # Translators: tooltip of the "Role" column header of a mission's sortie table (the combat role of the sortie)
    "role": _(
        "Attack if the aircraft carried bombs or rockets (or is a dedicated attacker), otherwise air superiority."
    ),
    # Translators: tooltip of the "Credit" column header of a table of kills (who gets the kill)
    "credit": _(
        "Kill: the attacker did the most damage. Assist: the attacker damaged it but someone else destroyed it. "
        "Friendly fire: both were on the same side."
    ),
    # Translators: tooltip of the "Damage dealt" column header of a sortie's damage table
    "damage_dealt": _(
        "Damage you did to this counterpart's aircraft (not to the pilot). Against players it is a percentage of "
        "their aircraft's health, at most 100% for each flight leg: hits after the aircraft was at 100% count "
        "nothing, and after a landing and a new takeoff (an assumed repair) the count starts from 0 again, so a "
        "sortie with a repair can show more than 100%. Against AI and ground objects it is the health units summed "
        "over all objects of that type, at most 1.0 for each object."
    ),
    # Translators: tooltip of the "Damage taken" column header of a sortie's damage table
    "damage_taken_from": _("Damage this counterpart did to you, measured as in the column Damage dealt."),
    # Translators: tooltip of the "Altitude" column header of a sortie's timeline. Angels = thousands of feet, as US
    # fighter controllers say it; metres on the Soviet side.
    "timeline_altitude": _(
        "Height above sea level where it happened, not above the ground (the logs have no terrain data). BLUFOR "
        "sorties show Angels (thousands of feet; hover for the exact feet), REDFOR sorties metres."
    ),
    # Translators: tooltip of the "Damage" column header of a sortie's timeline
    "timeline_damage": _(
        "Damage of a significant hit, as a percentage of the object's health: plus if you dealt it, minus if you "
        "took it."
    ),
    # Translators: tooltip of the "Used" column header of a sortie's ammunition table
    "ammo_used": _("Rounds fired: loaded minus left. A dash when unknown, for example after a resupply."),
    # Translators: tooltip of the "Targets damaged" column header of a sortie's ordnance table
    "ord_targets": _("Targets it damaged: a target damaged by two bombs counts twice."),
    # Translators: tooltip of the "Direct hits" column header of a sortie's ordnance table
    "ord_direct": _("Direct impacts logged with the ordnance named."),
    # Translators: tooltip of the "Kills" column header of an aircraft's "Hits to destroy" table. Each row is one
    # mix of ammunition types (a single type is a mix of one).
    "mix_instances": _(
        "Counted kills of this aircraft in which exactly these ammunition types hit. The first row counts them all."
    ),
    # Translators: tooltip of the "Average hits" column header of an aircraft's "Hits to destroy" table. "5.4 + 2.7"
    # is an example: the numbers follow the order of the ammunition types named in the row.
    "hits_average": _(
        "Average gun hits of each ammunition type that it took to shoot this aircraft down, in the order the types "
        "are named: 5.4 + 2.7 is 5.4 hits of the first type and 2.7 of the second."
    ),
    # Translators: tooltip of the "Sorties" column header of a streak table
    "streak_sorties": _("Sorties in a row that the pilot survived."),
    # Translators: tooltip of the "Air kills" column header of a streak table
    "streak_kills": _("Air kills made during the streak."),
    # Translators: tooltip of the "Ground kills" column header of a streak table
    "streak_kills_ground_run": _("Ground kills made during the streak."),
    # Translators: tooltip of the "Flight time" column header of a streak table
    "streak_time": _("Flight time added up over the streak's sorties."),
    # Translators: tooltip of the "Aircraft lost" column header of the profile's "Caused by" table
    "pve_lost": _("Sorties in which the aircraft was lost to this cause (the pilot may have survived)."),
    # Translators: tooltip of the optional "Elo (jet)" column header
    "elo_jet": _(
        "Air-to-air skill rating for jet aircraft, 1500 at the start, all time. It moves only when you and another "
        "player meet in air superiority sorties."
    ),
    # Translators: tooltip of the optional "Elo (prop)" column header
    "elo_prop": _(
        "Air-to-air skill rating for propeller aircraft, 1500 at the start, all time. It moves only when you and "
        "another player meet in air superiority sorties."
    ),
    # Translators: tooltip of the optional "Gun accuracy" column header of the player and aircraft lists
    "accuracy": _(
        "Gun hits per round fired, adding up only the sorties where the rounds fired are known. Bombs and rockets "
        "are not counted."
    ),
    # Translators: tooltip of the optional "Air accuracy" column header of the player and aircraft lists
    "accuracy_air": _(
        "Gun hits on aircraft per round fired, in air superiority sorties where the rounds fired are known."
    ),
    # Translators: tooltip of the optional "Ground accuracy" column header of the player and aircraft lists
    "accuracy_ground": _(
        "Gun hits on ground targets per round fired, in attack sorties where the rounds fired are known."
    ),
    # Translators: tooltip of the "Outcome" column header of the sortie tables
    "outcome": _("How the sortie ended for the aircraft: landed, shot down, crashed and so on."),
    # Translators: tooltip of the "Pilot fate" column header of the sortie tables
    "pilot_fate": _("What became of the pilot: survived, bailed out, captured or dead."),
    # Translators: tooltip of the "Air kills" column header
    "kills_air": _("Aircraft shot down, players and AI together. Assists are counted separately."),
    # Translators: tooltip of the "Ground kills" column header
    "kills_ground": _("Ground targets destroyed, static objects included. Fences do not count."),
    # Translators: tooltip of the "Flight time" column header
    "flight_time": _("Time in the air. Taxiing does not count."),
    # Translators: tooltip of the "Air kills (PvP)" column header
    "kills_air_pvp": _("Aircraft flown by other players that were shot down."),
    # Translators: tooltip of the "Air kills (AI)" column header
    "kills_air_ai": _("AI aircraft that were shot down."),
    # Translators: tooltip of the "Air score" column header of a sortie table: the score of that one sortie
    "air_points": _(
        "What this sortie added to the air score: points for air kills and assists and flight-time points if the "
        "server enables them, minus its penalties. The sortie page says what a penalty was for."
    ),
    # Translators: tooltip of the "Ground score" column header of a sortie table: the score of that one sortie
    "ground_points": _(
        "What this sortie added to the ground score: points for ground kills, minus its penalties. The sortie page "
        "says what a penalty was for."
    ),
    # Translators: tooltip of the "Loadout" column header (what the aircraft was armed with)
    "payload": _("What the aircraft was armed with."),
    # Translators: tooltip of the "Takeoffs" column header of a sortie table
    "takeoffs": _("Takeoffs in this sortie. An aircraft can land, rearm and fly on."),
    # Translators: tooltip of the "Landings" column header of a sortie table
    "landings": _("Landings in this sortie. An aircraft can land, rearm and fly on."),
    # Translators: tooltip of the "Deaths" column header
    "deaths": _("Sorties that ended with the pilot dead."),
    # Translators: tooltip of the "Bailouts" column header
    "bailouts": _("Sorties in which the pilot bailed out."),
    # Translators: tooltip of the "Pilots" column header of the aircraft list
    "pilots": _("Different pilots who flew this aircraft."),
    # Translators: tooltip of the "Air kills/h" column header of the aircraft list
    "kills_per_hour": _("Aircraft shot down per hour in the air."),
    # Translators: tooltip of the "Sorties per pilot" column header of the aircraft list
    "sorties_per_pilot": _("Average number of sorties each pilot flew in this aircraft."),
    # Translators: tooltip of the "Sorties per pilot" column header of the mission list
    "sorties_per_player": _("Average number of sorties each player flew in this mission, gunners counted as players."),
    # Translators: tooltip of the "REDFOR sorties" column header of the mission list
    "redfor_sorties": _("Sorties flown for REDFOR in this mission."),
    # Translators: tooltip of the "BLUFOR sorties" column header of the mission list
    "blufor_sorties": _("Sorties flown for BLUFOR in this mission."),
    # Translators: tooltip of the "Ended" column header of the mission list
    "ended": _("When the mission ended."),
    # Translators: tooltip of the "First seen" column header of the player list
    "first_seen": _("When this pilot first flew here."),
    # Translators: tooltip of the "Last seen" column header of the player list
    "last_seen": _("When this pilot last flew here."),
    # Translators: tooltip of the "Attack proficiency" column header of the player and aircraft tables (no ranking)
    "ground_hour": _("Ground score earned in attack sorties per hour spent on target."),
    # Translators: tooltip of the "Attack ground score" column header of the attack proficiency leaderboard
    "attack_score": _("Ground score earned in attack sorties only."),
    # Translators: tooltip of the "Average pilot Elo" column header of an aircraft's loadout and modification tables
    "average_elo": _(
        "Average Elo of the pilots behind these sorties, one vote per sortie: a pilot's rating in this aircraft type "
        "if they have one, else their jet or propeller rating. Pilots without a rating are left out."
    ),
    # Translators: tooltip of the "Share" column header of the profile's "Caused by" table
    "pve_death_share": _("This cause's share of all the pilot's deaths."),
    # Translators: tooltip of the "Share" column header of the profile's "Victim" table
    "pve_kill_share": _("This victim's share of all the pilot's kills."),
}


def registered(key: str) -> str:
    """The description registered under `key`, "" without one. A key is never text to show (see `hint_text`)."""
    return str(HINTS[key]) if key in HINTS else ""


def hint_text(hint: object) -> str:
    """The text of a header's `hint`: a key of `HINTS`, or a ready (possibly lazy) text such as a `Column.hint`."""
    if isinstance(hint, str) and hint in HINTS:
        return str(HINTS[hint])
    return str(hint) if hint else ""
