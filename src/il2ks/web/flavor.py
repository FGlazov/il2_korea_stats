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

from django.utils.translation import gettext_lazy

from il2ks.db.models import PlayerSortie
from il2ks.web.display import Label

MULTI_KILL_MIN = 3  # air kills in one sortie that earn a line
BADLY_DAMAGED = 0.5  # damage taken (share of the airframe's health) of a landing worth a remark

SPOTS: Mapping[str, tuple[Label, ...]] = {
    # Player profile, hall of shame (some taxi accidents or strafings on record).
    "shame_some": (
        gettext_lazy("Every pilot has bad days. These are the ones the server remembers."),
        gettext_lazy("The ground crew keeps a file on these. It is a thin, well-thumbed file."),
        gettext_lazy("Aviation's first rule: the airfield is also part of the mission."),
        gettext_lazy("Nobody is judging. The wreckage is merely taking notes."),
        gettext_lazy("Even the best squadrons have a story about the taxiway."),
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
        gettext_lazy("Ace-in-a-day territory. Scoreboard salutes."),
        gettext_lazy("Someone had a very good night at the office."),
        gettext_lazy("Fair winds, full magazines and the numbers to prove it."),
    ),
    # Home page, a mission where nobody scored.
    "nobody_scored": (
        gettext_lazy("A quiet one. Everybody got home with their ammunition."),
        gettext_lazy("The sky was empty of drama. The tea stayed hot."),
        gettext_lazy("No kills this time. Somebody had to keep the airspace tidy."),
    ),
    # Sortie page, one per notable outcome (see `sortie_spot`).
    "sortie_captured": (
        gettext_lazy("The silk opened, the welcoming committee was less friendly."),
        gettext_lazy("Landed by parachute, collected by the other side. The war goes on without this pilot, for now."),
        gettext_lazy("Bailed out and taken in. Hopefully the food is decent."),
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


def sortie_spot(sortie: PlayerSortie) -> str | None:
    """The spot for a sortie that deserves a line, most notable first; None for the ordinary ones (and for gunner
    sorties, whose numbers the log credits to the pilot)."""
    if sortie.role == "gunner":
        return None
    if sortie.taxi_accident:
        return "sortie_taxi"
    if sortie.friendly_kills:
        return "sortie_friendly_fire"
    if sortie.is_captured or sortie.pilot_status == "captured":
        return "sortie_captured"
    if sortie.outcome == "ditched":
        return "sortie_ditched"
    if sortie.outcome == "shot_down" and sortie.loss_class == "aaa":
        return "sortie_aa"
    if sortie.kills_air >= MULTI_KILL_MIN:
        return "sortie_ace"
    if sortie.outcome == "landed" and sortie.aircraft_status == "damaged" and sortie.damage_taken >= BADLY_DAMAGED:
        return "sortie_limped_home"
    return None
