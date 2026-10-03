"""PvE breakdown (FR-WEB-21): what class of counterpart is behind a player's loss.

One class per lost sortie, taken from the existing credit and loss-cause resolution (`Verdict.killer`, `loss_cause`),
never re-derived. See design_doc/13_game_rules.md, "PvE breakdown".
"""

from il2ks.core.replay.credit import hit_records
from il2ks.core.replay.judge import Verdict
from il2ks.core.replay.model import Party, SortieState, party_of
from il2ks.core.replay.result import LossClass


def party_class(party: Party, victim_coalition: int) -> LossClass:
    """The class of an attacking party, from the victim's point of view.

    - `friendly`: the party is on the victim's (non-zero) coalition, player or AI (same rule as `is_friendly`).
    - `player`: another player's sortie.
    - AI by catalog class of the root object (turret and crew fire is folded into it, `party_of`):
      `ai_aircraft` = fighter or attacker, `ai_gunner` = bomber or transport (their only weapons are defensive guns; the
      log never names a turret, so a gunner of an attacker type like the IL-10 can't be told from its forward guns),
      `aaa` = anti-aircraft (class `aaa` or ground category `aaa`, incl. flak cars), `ground` = tank, vehicle, ship or
      static object, `unknown` = anything else (an uncatalogued type)."""
    coalition = party.coalition
    if coalition is not None and coalition != 0 and coalition == victim_coalition:
        return "friendly"
    if isinstance(party, SortieState):
        return "player"
    info = party.info
    if info.cls in ("fighter", "attacker"):
        return "ai_aircraft"
    if info.cls in ("bomber", "transport"):
        return "ai_gunner"
    if info.cls == "aaa" or info.ground_category == "aaa":
        return "aaa"
    if info.cls in ("tank", "vehicle", "ship", "static"):
        return "ground"
    return "unknown"


def _most_hits(sortie: SortieState, upto_tick: int) -> Party | None:
    """The attacker with the most hit lines on the aircraft or pilot (first one on ties), for a loss that has attacker
    hits but no attacker damage (so `credit_kill` named nobody)."""
    hits: dict[Party, int] = {}
    for victim in (sortie.airframe, sortie.bot):
        for record in hit_records(victim, sortie, upto_tick, attackers_only=True):
            assert record.attacker is not None  # attackers_only
            party = party_of(record.attacker)
            hits[party] = hits.get(party, 0) + 1
    return max(hits, key=lambda p: hits[p]) if hits else None


def loss_class(sortie: SortieState, verdict: Verdict) -> LossClass | None:
    """Who is behind this sortie's loss or death; `None` when it lost nothing (`loss_cause` "none").

    `environment` = no attacker (`loss_cause` "self": crash, terrain, structural failure, own error, a disconnect or an
    abandoned aircraft nobody had hit). Otherwise the class of the credited killer (`Verdict.killer`), or of the
    attacker with the most hits when nobody has damage credit."""
    if verdict.loss_cause == "none":
        return None
    if verdict.loss_cause == "self":
        return "environment"
    killer = verdict.killer or _most_hits(sortie, verdict.cutoff_tick)
    return party_class(killer, sortie.coalition) if killer is not None else "unknown"
