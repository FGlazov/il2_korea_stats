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
# Assists that earn the stolen-kills line, by the sortie's own air kills (maintainer, 2026-10-04): 3+ with no air kill,
# 5+ with one, 6+ with two (three or more air kills is the ace line, which comes first).
STOLEN_ASSISTS_MIN = {0: 3, 1: 5}
STOLEN_ASSISTS_MIN_MORE_KILLS = 6
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
    ),
    # ... friendly fire only.
    "shame_friendly": (
        gettext_lazy("The markings are small and the sky is crowded. It happens to the best squadrons."),
        gettext_lazy("Wrong target, right enthusiasm. The debrief will be lively."),
        gettext_lazy("Friendly fire: proof that the guns work."),
        gettext_lazy("Everybody gets a wingman mixed up once. Some just have a longer record."),
    ),
    # ... both.
    "shame_both": (
        gettext_lazy("Trouble on the ramp and in the air. At least this pilot is consistent."),
        gettext_lazy("The ground crew and the squadron both have a file on this one. The files have met."),
        gettext_lazy("Every pilot has bad days. These are the ones the server remembers."),
        gettext_lazy("An all-rounder: the taxiway and the formation have both had their moments."),
    ),
    # Above the 90th percentile of pilots with enough sorties: gentle ribbing, nobody is shamed.
    "shame_taxi_p90": (
        gettext_lazy("Few pilots know the taxiway as intimately as this one. It could give guided tours."),
        gettext_lazy("The ramp crew has named a bollard after this pilot. Fondly."),
        gettext_lazy("Some chase kills. This one chases the perfect parking spot, and keeps looking."),
    ),
    "shame_friendly_p90": (
        gettext_lazy("A generous soul: this pilot shares the ammunition with everyone, wingmen included."),
        gettext_lazy("The squadron has learned to fly a little wider. Bold of them, and well chosen."),
        gettext_lazy("Equal-opportunity gunnery: this pilot never plays favourites between sides."),
    ),
    "shame_both_p90": (
        gettext_lazy("An ambassador for chaos, on the ground and in the air. The squadron would not swap them."),
        gettext_lazy("Taxiway, formation, it makes no difference: wherever this pilot goes, there is a story."),
        gettext_lazy("Top of the charts where no one aims to be. The mess bar has a tab open in this pilot's honour."),
    ),
    # Player profile, hall of shame (nothing on record).
    "shame_clean": (
        gettext_lazy("A clean sheet so far. The runway thanks you."),
        gettext_lazy("Spotless. The ground crew is almost suspicious."),
        gettext_lazy("Not one incident on the ramp. Textbook."),
        gettext_lazy("Nothing to see here, which is the best thing to see."),
    ),
    # Home page, under the top pilots of the last mission.
    "top_pilot": (
        gettext_lazy("Top of the board. The rest of the sky took notes."),
        gettext_lazy("Best showing of the mission. Well flown."),
        gettext_lazy("Someone had a very good night at the office."),
        gettext_lazy("Fair winds, full magazines and the numbers to prove it."),
    ),
    # Home page, a mission where nobody scored.
    "nobody_scored": (
        gettext_lazy("A quiet one. Everybody got home with their ammunition."),
        gettext_lazy("The sky was empty of drama. The tea stayed hot."),
        gettext_lazy("No kills this time. Somebody had to keep the airspace tidy."),
    ),
    # A tour with no sorties to show (profile, mission and sortie lists): a fresh start, never a reproach.
    "tour_empty": (
        gettext_lazy("A fresh tour: the log book is still blank. Go and write the first line."),
        gettext_lazy("Quiet skies so far. The airfield is yours."),
        gettext_lazy("Nothing here yet. The ground crew just finished polishing the aircraft."),
        gettext_lazy("Engines are warm and the sky is waiting. Start the first sortie of the tour!"),
        gettext_lazy("Not a single contrail yet. Somebody has to be first."),
    ),
    # Sortie page, one per notable outcome (see `sortie_spot`).
    "sortie_captured": (
        gettext_lazy("The silk opened, the welcoming committee was less friendly."),
        gettext_lazy("Landed by parachute, collected by the other side. The war goes on without this pilot, for now."),
        gettext_lazy("Out of the fight, but not out of the story."),
    ),
    "sortie_ditched": (
        gettext_lazy("Not a landing, not a crash: a firm disagreement with the ground."),
        gettext_lazy("The gear stayed up and so did the pilot's spirits."),
        gettext_lazy("A creative arrival. The airframe will be remembered fondly."),
    ),
    "sortie_aa": (
        gettext_lazy("The flak had the final say."),
        gettext_lazy("Those little black clouds were not decorative."),
        gettext_lazy("Ground fire: the one opponent that never needs fuel."),
    ),
    "sortie_friendly_fire": (
        gettext_lazy("Wrong target, right enthusiasm. The sky is crowded, mistakes happen."),
        gettext_lazy("The sky is crowded and the markings are small. It happens."),
        gettext_lazy("Friendly fire happens to the best squadrons. The debrief will be lively."),
    ),
    "sortie_taxi": (
        gettext_lazy("The war was waiting. The taxiway had other ideas."),
        gettext_lazy("Never left the ground, still earned a story."),
        gettext_lazy("Most dangerous part of the mission: the first hundred metres."),
    ),
    "sortie_ace": (
        gettext_lazy("Busy sortie. The enemy count went down noticeably."),
        gettext_lazy("The gun camera footage on this one would be a good watch."),
        gettext_lazy("Several kills in one flight. Time to buy the squadron a round."),
    ),
    # Extreme events (thresholds above; order in `sortie_spot`).
    "sortie_ai_gunner": (
        gettext_lazy("A bomber's tail gunner had a say in this. Those guns are not decoration."),
        gettext_lazy("Outshot by a gunner who never gets a break. Respect the tail."),
        gettext_lazy("The rear gunner had the last word, and it was a loud one."),
    ),
    "sortie_bomber_hunter": (
        gettext_lazy("Several bombers will not be making it home tonight. Fine hunting."),
        gettext_lazy("The big, slow ones had a bad day, and this pilot had a good one."),
        gettext_lazy("Bomber hunting is a craft, and this sortie was a masterclass."),
        gettext_lazy("Somebody's bombing run ended early. Somebody else is buying the next round."),
    ),
    "sortie_stolen_kills": (
        gettext_lazy("Ah, all your kills got stolen! The wingmen send their thanks, and nothing else."),
        gettext_lazy("So many assists, so few credits. The kill counter is a cruel bookkeeper."),
        gettext_lazy(
            "Softened them up beautifully and the flight collected the trophies. Next time, the last shot is yours."
        ),
        gettext_lazy(
            "The enemy was well and truly damaged. Somebody else just happened to be standing at the finish line."
        ),
    ),
    "sortie_battered_victor": (
        gettext_lazy("Riddled, bruised and still scoring. The crew chief wants a word, then a handshake."),
        gettext_lazy("It came home full of holes and full of results. A fair trade."),
        gettext_lazy("The airframe took a beating and handed one back."),
    ),
    "sortie_ground_pounder": (
        gettext_lazy("Not much was left standing down there. The map needs a fresh coat of paint."),
        gettext_lazy("Everything with a motor, a barrel or a roof took a hit. Thorough work."),
        gettext_lazy("A very busy day for the ground crews on the other side."),
        gettext_lazy("The ground below will remember this one for a while."),
    ),
    "sortie_quick_kill": (
        gettext_lazy("Barely off the runway and already on the scoreboard. That is a fast start."),
        gettext_lazy("The wheels were still folding when the first kill reached the log."),
        gettext_lazy("No time wasted. The coffee in the tower was still warm."),
        gettext_lazy("A first kill that quick deserves a stopwatch and a small medal."),
    ),
    "sortie_marathon": (
        gettext_lazy("A long day at the office. The fuel gauge is full of respect."),
        gettext_lazy("Over an hour in the air. The ground crew nearly sent out a search party."),
        gettext_lazy("An endurance flight: the seat cushion deserves a mention in the report."),
        gettext_lazy("Some pilots sprint. This one went the distance."),
    ),
    "sortie_limped_home": (
        gettext_lazy("It still flew, mostly out of politeness. Well landed."),
        gettext_lazy("More hole than airplane, and still back on the ground. Hats off."),
        gettext_lazy("The crew chief will have opinions about this one."),
    ),
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

    taxi accident, friendly fire, captured, shot down by an AI gunner, ditched, flak, bomber hunter, ace, stolen kills,
    battered victor, limped home, ground pounder, quick first kill, marathon.

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
    if sortie.outcome == "shot_down" and sortie.loss_class == "aaa":
        return "sortie_aa"
    if highlights is not None and highlights.bomber_kills >= BOMBER_KILLS_MIN:
        return "sortie_bomber_hunter"
    if sortie.kills_air >= MULTI_KILL_MIN:
        return "sortie_ace"
    if sortie.assists >= STOLEN_ASSISTS_MIN.get(sortie.kills_air, STOLEN_ASSISTS_MIN_MORE_KILLS):
        return "sortie_stolen_kills"
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
        friendly_fire_incidents=stats.friendly_fire_incidents,
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
    friendly = stats.friendly_fire_incidents > 0
    top_taxi = taxi and _is_top("taxi_per_sortie", stats, marks)
    top_friendly = friendly and _is_top("friendly_fire_per_sortie", stats, marks)
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
