"""Flavor text (FR-WEB-23): a few light-hearted one-liners at hand-picked "highlight" spots, never everywhere.

Every spot has several variants (all translatable). The variant is chosen from a stable seed (the object's id plus the
spot name, hashed), so a page never changes on reload, every language shows the same variant, and HTTP caching
(`ETag`/`Last-Modified`) stays valid. Templates use `{% flavor "spot" seed %}` (il2ks template tags).
The sortie page asks `sortie_spot` which notable outcome, if any, deserves a line.

Tone: respectful, light military humour, never harsh about the player, no real-world politics. Add variants freely (a
new string needs `uv run il2ks dev translations update`); add a spot by adding it to `SPOTS` and to the template.
"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from django.utils.translation import gettext_lazy

from il2ks.core import stat_marks
from il2ks.db.models import Counters, PlayerSortie, StatThreshold
from il2ks.web.display import Label

MULTI_KILL_MIN = 3  # air kills in one sortie that earn a line
BADLY_DAMAGED = 0.5  # damage taken (share of the airframe's health) of a landing worth a remark
# Thresholds of the extreme-event spots, read off the September 2026 archive (15,245 pilot sorties; share of those
# sorties that reach the threshold, before the precedence in `sortie_spot` takes some away):
BOMBER_KILLS_MIN = 2  # bomber / attacker / transport air kills: 0.22% (one such kill is 1.84%, too common for a title)
# Air assists (assists on aircraft) that earn the stolen-kills line, by the sortie's own air kills (maintainer,
# 2026-10-04): 2+ with no air kill, 3+ with one, 6+ with two (three or more air kills is the ace line, which comes
# first). Ground assists have their own line. Of the 15,245 September pilot sorties only 291 have an air assist at all,
# so the line stays rare.
STOLEN_ASSISTS_MIN = {0: 2, 1: 3}
STOLEN_ASSISTS_MIN_MORE_KILLS = 6
# Ground assists: at least 5 and at least as many as the sortie's own ground kills (the pilot did as much damage to
# targets others finished as to those it finished itself): 1.5% of the 8,300 attack sorties (0.8% of all sorties); 5 is
# the p75 and 10 the p90 of the sorties that have any ground assist (1,684 sorties, 11%). Not for 70+ ground kills (the
# ground pounder line, below, fits better).
STOLEN_GROUND_ASSISTS_MIN = 5
QUICK_KILL_S = 420.0  # first air kill within 7 min of takeoff: 1.1%
MARATHON_S = 3600.0  # flight time of 1 h or more: 1.0%
BATTERED_KILLS_MIN = 2  # kills (air + ground) of a landing with BADLY_DAMAGED damage: 1.1%
GROUND_KILLS_MIN = 70  # ground kills in one sortie: 2.0%


@dataclass(frozen=True, slots=True)
class Highlights:
    """What a sortie's spots need beyond its own columns, read from its timeline (`sortie_view.build_highlights`):
    air kills on bomber-type aircraft, and seconds from takeoff (or the spawn of an air start) to the first air kill."""

    bomber_kills: int = 0
    first_kill_s: float | None = None


SPOTS: Mapping[str, tuple[Label, ...]] = {
    # Player profile, hall of shame: runway or taxi accidents only.
    "shame_taxi": (
        gettext_lazy("The airfield is also part of the mission. Some pilots just take that very seriously."),
        gettext_lazy("The taxiway keeps a file on these. It is a thin, well-thumbed file."),
        gettext_lazy("The war was waiting. The ramp had other ideas."),
        gettext_lazy("Nobody is judging. The wreckage is merely taking notes."),
        gettext_lazy("Tower to pilot: the war is the other way, but thank you for the thorough survey of the apron."),
        gettext_lazy("Mechanic's note: the aircraft is fine, the fence is not. Please see the fence."),
    ),
    # ... friendly fire only.
    "shame_friendly": (
        gettext_lazy("The markings are small and the sky is crowded. It happens to the best squadrons."),
        gettext_lazy("Wrong target, right enthusiasm. The debrief will be lively."),
        gettext_lazy("Friendly fire: proof that the guns work."),
        gettext_lazy("Everybody gets a wingman mixed up once. Some just have a longer record."),
        gettext_lazy("Radio check: that was a friendly. Yes, the voice on the other end sounded rather upset."),
        gettext_lazy("A tip of the hat to the wingman who forgave it. Most of them do, eventually."),
    ),
    # ... both.
    "shame_both": (
        gettext_lazy("Trouble on the ramp and in the air. At least this pilot is consistent."),
        gettext_lazy("The ground crew and the squadron both have a file on this one. The files have met."),
        gettext_lazy("Every pilot has bad days. These are the ones the server remembers."),
        gettext_lazy("An all-rounder: the taxiway and the formation have both had their moments."),
        gettext_lazy("Two departments, one pilot. The paperwork is going to need a bigger folder."),
        gettext_lazy("Somewhere a quartermaster is sighing, and he is not even sure which incident it was for."),
    ),
    # Above the 90th percentile of pilots with enough sorties: gentle ribbing, nobody is shamed.
    "shame_taxi_p90": (
        gettext_lazy("Few pilots know the taxiway as intimately as this one. It could give guided tours."),
        gettext_lazy("The ramp crew has named a bollard after this pilot. Fondly."),
        gettext_lazy("Some chase kills. This one chases the perfect parking spot, and keeps looking."),
        gettext_lazy("Technically the most experienced pilot on the apron. Nobody has spent more time there."),
        gettext_lazy("The ground crew keeps a spare wingtip with this pilot's name on it. Just in case."),
        gettext_lazy("A pilot of rare devotion: the airfield is never far from this one's heart, or its propeller."),
    ),
    "shame_friendly_p90": (
        gettext_lazy("A generous soul: this pilot shares the ammunition with everyone, wingmen included."),
        gettext_lazy("The squadron has learned to fly a little wider. Bold of them, and well chosen."),
        gettext_lazy("Equal-opportunity gunnery: this pilot never plays favourites between sides."),
        gettext_lazy("Somebody has to keep the wingmen alert. This pilot volunteers every time."),
        gettext_lazy("The squadron's emergency drills have, thanks to this pilot, never been short of material."),
        gettext_lazy("Brave enough to shoot first and sort out the colours afterwards. The mess salutes the nerve."),
    ),
    "shame_both_p90": (
        gettext_lazy("An ambassador for chaos, on the ground and in the air. The squadron would not swap them."),
        gettext_lazy("Taxiway, formation, it makes no difference: wherever this pilot goes, there is a story."),
        gettext_lazy("Top of the charts where no one aims to be. The mess bar has a tab open in this pilot's honour."),
        gettext_lazy("The record books call it a pattern. The squadron calls it character."),
        gettext_lazy("Legend has it the tower flinches whenever this callsign comes over the radio."),
        gettext_lazy("Rarely does one pilot give the safety officer so much to write about. A true collaborator."),
    ),
    # Player profile, hall of shame (nothing on record).
    "shame_clean": (
        gettext_lazy("A clean sheet so far. The runway thanks you."),
        gettext_lazy("Spotless. The ground crew is almost suspicious."),
        gettext_lazy("Not one incident on the ramp. Textbook."),
        gettext_lazy("Nothing to see here, which is the best thing to see."),
        gettext_lazy("Mechanic's logbook, this pilot's page: no entries. We keep it as a reference."),
        gettext_lazy("Zero scrapes, zero misfires at friends. Wingmen sleep well around this one."),
    ),
    # Home page, under the top pilots of the last mission.
    "top_pilot": (
        gettext_lazy("Top of the board. The rest of the sky took notes."),
        gettext_lazy("Best showing of the mission. Well flown."),
        gettext_lazy("Someone had a very good night at the office."),
        gettext_lazy("Fair winds, full magazines and the numbers to prove it."),
        gettext_lazy("Another night, another name at the top. The hangar wall is running out of space for it."),
        gettext_lazy("Chapeau. The rest of the sky is now taking requests on how to do that."),
    ),
    # Home page, a mission where nobody scored.
    "nobody_scored": (
        gettext_lazy("A quiet one. Everybody got home with their ammunition."),
        gettext_lazy("The sky was empty of drama. The tea stayed hot."),
        gettext_lazy("No kills this time. Somebody had to keep the airspace tidy."),
        gettext_lazy("Radio log: nothing to report. The operator has gone through two crosswords."),
        gettext_lazy("Plenty of clouds, plenty of fuel burnt, not a shot fired in anger. The weather won."),
        gettext_lazy("Everyone patrolled bravely, and the enemy patrolled somewhere else. Maybe next time."),
    ),
    # A tour with no sorties to show (profile, mission and sortie lists): a fresh start, never a reproach.
    "tour_empty": (
        gettext_lazy("A fresh tour: the log book is still blank. Go and write the first line."),
        gettext_lazy("Quiet skies so far. The airfield is yours."),
        gettext_lazy("Nothing here yet. The ground crew just finished polishing the aircraft."),
        gettext_lazy("Engines are warm and the sky is waiting. Start the first sortie of the tour!"),
        gettext_lazy("Not a single contrail yet. Somebody has to be first."),
        gettext_lazy("Fresh paint, full tanks, an empty chalkboard. The briefing room awaits its first pilot."),
    ),
    # Sortie page, one per notable outcome (see `sortie_spot`).
    "sortie_captured": (
        gettext_lazy("The silk opened, the welcoming committee was less friendly."),
        gettext_lazy("Landed by parachute, collected by the other side. The war goes on without this pilot, for now."),
        gettext_lazy("Out of the fight, but not out of the story."),
        gettext_lazy("Unscheduled landing in unfamiliar territory. The flight plan did not cover this chapter."),
        gettext_lazy("The aircraft is lost, the pilot is still in the story. Sequels have started worse."),
        gettext_lazy("Off the squadron roster for the moment. The empty chair at the mess table stays reserved."),
    ),
    "sortie_ditched": (
        gettext_lazy("Not a landing, not a crash: a firm disagreement with the ground."),
        gettext_lazy("The gear stayed up and so did the pilot's spirits."),
        gettext_lazy("A creative arrival. The airframe will be remembered fondly."),
        gettext_lazy("Controlled flight into a field. The farmer has questions, the pilot has a story."),
        gettext_lazy("Mustang, meet meadow. Meadow, Mustang. It was short, but the introductions were firm."),
        gettext_lazy("A forced landing is still a landing. The log says so, and the log is never wrong."),
    ),
    # Destroyed on the ground by an attacker: never took off (parked) ...
    "sortie_strafed": (
        gettext_lazy("Destroyed before it ever left the ramp. The ground crew is holding a very short memorial."),
        gettext_lazy("Not even the engine had warmed up yet. Sometimes the war simply comes to you."),
        gettext_lazy("Bad luck, not bad flying: the airframe never got a chance to prove itself."),
        gettext_lazy("The ground crew mourns the airframe, and quietly polishes the next one."),
        gettext_lazy(
            "The engine never got to say a word. The ground crew would like it noted that the aircraft was ready."
        ),
        gettext_lazy("A low pass by the other side, uninvited. The ramp is not a place for opinions."),
    ),
    # ... or landed first and was caught on the ground.
    "sortie_strafed_landed": (
        gettext_lazy("Home at last, and the ramp was not safe either. The ground crew mourns the airframe."),
        gettext_lazy("Wheels down, engine barely cooling, and then the enemy arrived. Rotten luck."),
        gettext_lazy("Survived the sky, caught on the ramp. Nobody should be strafed on their way to the hangar."),
        gettext_lazy("Landing was the easy part. The taxiway had a visitor with other plans."),
        gettext_lazy("Safe on the ground, then a late guest at the party. The tower would have liked a warning too."),
        gettext_lazy("Flew home through the worst of it, parked, and the worst of it followed. Nobody earns that."),
    ),
    "sortie_aa": (
        gettext_lazy("The flak had the final say."),
        gettext_lazy("Those little black clouds were not decorative."),
        gettext_lazy("Ground fire: the one opponent that never needs fuel."),
        gettext_lazy("Fourteen guns on the hill and one aircraft over it. The arithmetic was never on your side."),
        gettext_lazy("Low and slow near the gun line is the oldest mistake in the book. It is a very long book."),
        gettext_lazy("Flak never misses by much, which is the whole trouble with it."),
    ),
    "sortie_friendly_fire": (
        gettext_lazy("Wrong target, right enthusiasm. The sky is crowded, mistakes happen."),
        gettext_lazy("The sky is crowded and the markings are small. It happens."),
        gettext_lazy("Friendly fire happens to the best squadrons. The debrief will be lively."),
        gettext_lazy("Radio chatter, afterwards: 'Was that one of ours?' It was. The apologies are in the post."),
        gettext_lazy("In the heat of the moment a Sabre looks like a Sabre, until it turns out to be a squadron-mate."),
        gettext_lazy("It was a very good shot at the wrong target. The aim is not the problem here."),
    ),
    "sortie_taxi": (
        gettext_lazy("The war was waiting. The taxiway had other ideas."),
        gettext_lazy("Never left the ground, still earned a story."),
        gettext_lazy("Most dangerous part of the mission: the first hundred metres."),
        gettext_lazy("Ground speed: modest. Damage: not modest. The wingtip has already filed a complaint."),
        gettext_lazy("Before the Yalu, before the Sabres, there was the apron. It won this round."),
        gettext_lazy("The brakes and the pilot had a difference of opinion. Brakes: 1, pilot: 0."),
    ),
    "sortie_ace": (
        gettext_lazy("Busy sortie. The enemy count went down noticeably."),
        gettext_lazy("The gun camera footage on this one would be a good watch."),
        gettext_lazy("Several kills in one sortie. Time to buy the squadron a round."),
        gettext_lazy("Three or more in one go? The MiG Alley regulars would have raised an eyebrow, and then a glass."),
        gettext_lazy("The armourer is already counting the missing rounds, and smiling while he does it."),
        gettext_lazy("A busy hour for the gun camera and a quiet one for the other side's wingmen."),
    ),
    # Extreme events (thresholds above; order in `sortie_spot`).
    "sortie_ai_gunner": (
        gettext_lazy("A bomber's tail gunner had a say in this. Those guns are not decoration."),
        gettext_lazy("Outshot by a gunner who never gets a break. Respect the tail."),
        gettext_lazy("The rear gunner had the last word, and it was a loud one."),
        gettext_lazy("Closing from six o'clock on a bomber is a bold plan. The tail gunner enjoyed it too."),
        gettext_lazy("Tracers going the other way for once. The bomber crew will tell this one for years."),
        gettext_lazy("Bombers are not defenceless, and this one wanted it known."),
    ),
    "sortie_bomber_hunter": (
        gettext_lazy("Several bombers will not be making it home tonight. Fine hunting."),
        gettext_lazy("The big, slow ones had a bad day, and this pilot had a good one."),
        gettext_lazy("Bomber hunting is a craft, and this sortie was a masterclass."),
        gettext_lazy("Somebody's bombing run ended early. Somebody else is buying the next round."),
        gettext_lazy("A big formation, a long chase, and not much left of the formation. Textbook interception."),
        gettext_lazy("The target list said bombers, plural. The pilot read it literally."),
    ),
    "sortie_first_blood": (
        gettext_lazy("First blood of the mission goes to this pilot. Somebody had to open the scoring."),
        gettext_lazy("The first kill of the night, and it was this one. Everybody else is playing catch-up."),
        gettext_lazy("Drew first blood, and the scoreboard has not been the same since."),
        gettext_lazy("The mission was young and the sky was quiet, until this pilot changed that."),
        gettext_lazy("Opening shots matter, and these ones found their mark."),
        gettext_lazy("Somebody had to go first. It was a good choice."),
    ),
    "sortie_stolen_kills": (
        gettext_lazy("Ah, the finishing shots went to somebody else! The wingmen send their thanks, and nothing else."),
        gettext_lazy("So many assists, so few credits. The kill counter is a cruel bookkeeper."),
        gettext_lazy(
            "Softened them up beautifully and the flight collected the trophies. Next time, the last shot is yours."
        ),
        gettext_lazy(
            "The enemy was well and truly damaged. Somebody else just happened to be standing at the finish line."
        ),
        gettext_lazy("Half the credit, all of the effort. The claims officer has been sent a polite note."),
        gettext_lazy("The aircraft took the beating, somebody else took the bow. That is formation flying for you."),
    ),
    "sortie_stolen_ground": (
        gettext_lazy("You wore those targets down and somebody else finished them. Next time, the last shot is yours."),
        gettext_lazy("Plenty of ground targets carry your handiwork and somebody else's name in the log."),
        gettext_lazy(
            "Wore the convoy down beautifully, and the credit went to whoever fired last. A cruel bookkeeper."
        ),
        gettext_lazy("The ground crews on the other side know exactly whose work this was, even if the log does not."),
        gettext_lazy("Half the convoy was already smoking when somebody else arrived to take the credit. Typical."),
        gettext_lazy("The ground below was well tended, and the harvest went to a neighbour's barn."),
    ),
    "sortie_battered_victor": (
        gettext_lazy("Riddled, bruised and still scoring. The crew chief wants a word, then a handshake."),
        gettext_lazy("It came home full of holes and full of results. A fair trade."),
        gettext_lazy("The airframe took a beating and handed one back."),
        gettext_lazy("The airframe came home smelling of cordite and looking like a colander. Mission accomplished."),
        gettext_lazy("Tower: 'Say condition.' Pilot: 'Mostly air.' And with a good score on the board."),
        gettext_lazy("Every hole in it is a story, and the scoreboard backs up the better ones."),
    ),
    "sortie_ground_pounder": (
        gettext_lazy("Not much was left standing down there. The map needs a fresh coat of paint."),
        gettext_lazy("Everything with a motor, a barrel or a roof took a hit. Thorough work."),
        gettext_lazy("A very busy day for the ground crews on the other side."),
        gettext_lazy("The ground below will remember this one for a while."),
        gettext_lazy("An Il-10 would have been proud of this one. The Shturmovik school of thought lives."),
        gettext_lazy("The target list is shorter and the ammunition list is longer. A sound exchange."),
    ),
    "sortie_quick_kill": (
        gettext_lazy("Barely off the runway and already on the scoreboard. That is a fast start."),
        gettext_lazy("The wheels were still folding when the first kill reached the log."),
        gettext_lazy("No time wasted. The coffee in the tower was still warm."),
        gettext_lazy("A first kill that quick deserves a stopwatch and a small medal."),
        gettext_lazy("Quicker than the kettle in the ready room. The tea is still brewing."),
        gettext_lazy("The enemy had just finished its own takeoff checklist. A pity, really."),
    ),
    "sortie_marathon": (
        gettext_lazy("A long day at the office. The fuel gauge is full of respect."),
        gettext_lazy("Over an hour in the air. The ground crew nearly sent out a search party."),
        gettext_lazy("An endurance flight: the seat cushion deserves a mention in the report."),
        gettext_lazy("Some pilots sprint. This one went the distance."),
        gettext_lazy("Hours aloft and the weather chart is now mostly a diary. Landing was a well-earned one."),
        gettext_lazy(
            "Navigators on three continents would admire the fuel planning. The coffee flask is empty, naturally."
        ),
    ),
    "sortie_limped_home": (
        gettext_lazy("It still flew, mostly out of politeness. Well landed."),
        gettext_lazy("More hole than airplane, and still back on the ground. Hats off."),
        gettext_lazy("The crew chief will have opinions about this one."),
        gettext_lazy("The hydraulics sent their regards, the engine coughed, and the runway came up anyway."),
        gettext_lazy("It brought the pilot home, and that is the only entry the logbook needs to read."),
        gettext_lazy("Held together by good luck and the crew chief's optimism. Both held out."),
    ),
}


# Where each spot shows, for the admin's Quips page (plain words; translatable). Every spot of SPOTS needs one (a test).
SPOT_DESCRIPTIONS: Mapping[str, Label] = {
    "shame_taxi": gettext_lazy("Pilot profile, hall of shame: taxi accidents only"),
    "shame_friendly": gettext_lazy("Pilot profile, hall of shame: friendly fire only"),
    "shame_both": gettext_lazy("Pilot profile, hall of shame: taxi accidents and friendly fire"),
    "shame_taxi_p90": gettext_lazy("Pilot profile, hall of shame: taxi accidents among the top 10% of pilots"),
    "shame_friendly_p90": gettext_lazy("Pilot profile, hall of shame: friendly fire among the top 10% of pilots"),
    "shame_both_p90": gettext_lazy("Pilot profile, hall of shame: both, among the top 10% of pilots"),
    "shame_clean": gettext_lazy("Pilot profile, hall of shame: clean sheet"),
    "top_pilot": gettext_lazy("Home page: under the top pilots of the last mission"),
    "nobody_scored": gettext_lazy("Home page: the last mission had no kills"),
    "tour_empty": gettext_lazy("Lists of a tour without sorties: fresh start"),
    "sortie_captured": gettext_lazy("Sortie: the pilot was captured"),
    "sortie_ditched": gettext_lazy("Sortie: ditched (forced landing)"),
    "sortie_strafed": gettext_lazy("Sortie: destroyed on the ramp before takeoff"),
    "sortie_strafed_landed": gettext_lazy("Sortie: landed, then destroyed on the ground"),
    "sortie_aa": gettext_lazy("Sortie: shot down by flak"),
    "sortie_friendly_fire": gettext_lazy("Sortie: friendly fire"),
    "sortie_taxi": gettext_lazy("Sortie: taxi accident"),
    "sortie_ace": gettext_lazy("Sortie: three or more air kills"),
    "sortie_ai_gunner": gettext_lazy("Sortie: shot down by a bomber's AI gunner"),
    "sortie_bomber_hunter": gettext_lazy("Sortie: two or more bomber kills"),
    "sortie_first_blood": gettext_lazy("Sortie: the first kill of the mission"),
    "sortie_stolen_kills": gettext_lazy("Sortie: many air assists, few kills"),
    "sortie_stolen_ground": gettext_lazy("Sortie: many ground assists, few ground kills"),
    "sortie_battered_victor": gettext_lazy("Sortie: landed badly damaged, with kills"),
    "sortie_ground_pounder": gettext_lazy("Sortie: 70 or more ground kills"),
    "sortie_quick_kill": gettext_lazy("Sortie: first kill within seven minutes of takeoff"),
    "sortie_marathon": gettext_lazy("Sortie: an hour or more in the air"),
    "sortie_limped_home": gettext_lazy("Sortie: landed badly damaged"),
}


def pick(spot: str, seed: object) -> Label:
    """The variant of `spot` that `seed` selects, always the same for the same pair (SHA-256, not Python's per-process
    `hash`). Raises `KeyError` for an unknown spot, so a typo in a template fails loudly."""
    variants = SPOTS[spot]
    digest = hashlib.sha256(f"{spot}:{seed}".encode()).digest()
    return variants[int.from_bytes(digest[:8], "big") % len(variants)]


def sortie_spot(sortie: PlayerSortie, highlights: Highlights | None = None) -> str | None:
    """The spot for a sortie that deserves a line; None for the ordinary ones (and for gunner sorties, whose numbers
    the log credits to the pilot). The first match wins, in this order (accidents and losses first, then the rarest
    achievements, then the broader ones):

    taxi accident, friendly fire, captured, shot down by an AI gunner, ditched, strafed on the ground (after a landing
    or before takeoff), flak, bomber hunter, first blood, ace, stolen kills,
    stolen ground targets, battered victor, limped home, ground pounder, quick first kill, marathon.

    `highlights` carries what only the timeline knows (bomber kills, time to the first kill); without it those two
    spots are skipped."""
    if sortie.role == "gunner":
        return None
    if sortie.taxi_accident:
        return "sortie_taxi"
    if sortie.friendly_kills:
        return "sortie_friendly_fire"
    if sortie.is_captured or sortie.pilot_status == "captured":
        return "sortie_captured"
    if sortie.outcome == "shot_down" and sortie.loss_class == "ai_gunner":
        return "sortie_ai_gunner"
    if sortie.outcome == "ditched":
        return "sortie_ditched"
    if sortie.strafed_on_ground:
        return "sortie_strafed_landed" if sortie.landings else "sortie_strafed"
    if sortie.outcome == "shot_down" and sortie.loss_class == "aaa":
        return "sortie_aa"
    if highlights is not None and highlights.bomber_kills >= BOMBER_KILLS_MIN:
        return "sortie_bomber_hunter"
    if sortie.first_blood:
        return "sortie_first_blood"
    if sortie.kills_air >= MULTI_KILL_MIN:
        return "sortie_ace"
    if sortie.assists_air >= STOLEN_ASSISTS_MIN.get(sortie.kills_air, STOLEN_ASSISTS_MIN_MORE_KILLS):
        return "sortie_stolen_kills"
    if (
        sortie.assists_ground >= STOLEN_GROUND_ASSISTS_MIN
        and sortie.assists_ground >= sortie.kills_ground
        and sortie.kills_ground < GROUND_KILLS_MIN
    ):
        return "sortie_stolen_ground"
    landed_damaged = (
        sortie.outcome == "landed" and sortie.aircraft_status == "damaged" and sortie.damage_taken >= BADLY_DAMAGED
    )
    if landed_damaged and sortie.kills_air + sortie.kills_ground >= BATTERED_KILLS_MIN:
        return "sortie_battered_victor"
    if landed_damaged:
        return "sortie_limped_home"
    if sortie.kills_ground >= GROUND_KILLS_MIN:
        return "sortie_ground_pounder"
    if highlights is not None and highlights.first_kill_s is not None and highlights.first_kill_s <= QUICK_KILL_S:
        return "sortie_quick_kill"
    if sortie.flight_time_s >= MARATHON_S:
        return "sortie_marathon"
    return None


def stat_marks_totals(stats: Counters) -> stat_marks.Totals:
    """The counters the stat-mark metrics are made of, from a `Player` / `PlayerTour` row."""
    return stat_marks.Totals(
        sorties=stats.sorties,
        deaths=stats.deaths,
        planes_lost=stats.planes_lost,
        kills_air=stats.kills_air,
        kills_ground=stats.kills_ground,
        flight_time_s=stats.flight_time_s,
        taxi_accidents=stats.taxi_accidents,
        friendly_kills=stats.friendly_kills,
        time_on_target_s=stats.time_on_target_s,
        # Player and PlayerTour rows carry the scores, other Counters rows don't
        score_air=getattr(stats, "score_air", 0.0),
        score_ground=getattr(stats, "score_ground", 0.0),
        score_ground_attack=getattr(stats, "score_ground_attack", 0.0),
        flight_time_air_s=stats.flight_time_air_s,
        kills_intercept=stats.kills_intercept,
        kills_tank_attack=stats.kills_tank_attack,
    )


def _is_top(metric: stat_marks.Metric, stats: Counters, marks: Mapping[str, StatThreshold]) -> bool:
    """Above the p90 of the pilots with enough sorties (never for a pilot under that minimum, or a scope without
    thresholds)."""
    limits = marks.get(metric)
    if limits is None or stats.sorties < limits.min_sorties:
        return False
    value = stat_marks.metric_value(metric, stat_marks_totals(stats))
    found = stat_marks.Thresholds(limits.p10, limits.p25, limits.p50, limits.p75, limits.p90, limits.population)
    return stat_marks.band(value, found) == "top"


def shame_spot(stats: Counters, marks: Mapping[str, StatThreshold]) -> str:
    """The profile's hall-of-shame spot (doc 13): which incidents the pilot has (taxi accidents, friendly-fire
    sorties), and a separate, gentler spot for those whose rate per sortie is above the p90 of the pilots with enough
    sorties (`marks`, the thresholds of the scope `stats` belongs to). Only the incident kinds the pilot is in the top
    10% for are named in a p90 spot."""
    taxi = stats.taxi_accidents > 0
    friendly = stats.friendly_kills > 0
    top_taxi = taxi and _is_top("taxi_per_sortie", stats, marks)
    top_friendly = friendly and _is_top("friendly_kill_rate", stats, marks)
    if top_taxi and top_friendly:
        return "shame_both_p90"
    if top_taxi:
        return "shame_taxi_p90"
    if top_friendly:
        return "shame_friendly_p90"
    if taxi and friendly:
        return "shame_both"
    if taxi:
        return "shame_taxi"
    if friendly:
        return "shame_friendly"
    return "shame_clean"
