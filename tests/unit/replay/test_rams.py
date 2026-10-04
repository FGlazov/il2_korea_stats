"""Rams (OQ-61, `[rules] credit_rams`): two enemy aircraft that collide in the air credit each other a kill.

The log has no collision event: a ram is two aircraft destroyed in the air by the environment (AID -1) at the same
moment and place. Validated on the 210 sample missions (design_doc/13_game_rules.md, Rams)."""

import pytest

from il2ks.core.logparse.events import Pos
from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.rams import detect_rams
from il2ks.core.replay.result import MissionResult
from il2ks.core.replay.state import Replay
from il2ks.core.replay.toggles import RuleToggles
from tests.unit.replay.builder import FAR, NO, FakeCatalog, Scenario, by_acct

CREDIT = ReplayRules(toggles=RuleToggles(credit_rams=True))
BESIDE = Pos(FAR.x + 6.0, FAR.y, FAR.z)


def _collision(sc: Scenario, t: float = 110, gap: float = 0.04, apart: Pos = BESIDE) -> None:
    """A (aircraft 100) and B (200) are both destroyed by the environment, `gap` seconds apart."""
    sc.kill(t, NO, 100, pos=FAR)
    sc.kill(t + gap, NO, 200, pos=apart)
    sc.end(t + gap + 0.1, 100, 101)  # both sorties end right after (AType 4)
    sc.end(t + gap + 0.1, 200, 201)


def _two_players() -> Scenario:
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    return sc


def _detected(sc: Scenario, rules: ReplayRules = CREDIT) -> int:
    replay = Replay(FakeCatalog(), rules)
    for event in sc.events:
        replay.feed(event)
    replay.finish()
    return len(detect_rams(replay.facts, rules))


def _kills(result: MissionResult) -> list[tuple[int | None, int | None]]:
    return [(k.killer_sortie_index, k.victim_sortie_index) for k in result.kills if k.credit == "kill"]


def test_default_is_on_and_switching_it_off_credits_nobody() -> None:
    """OQ-89: `credit_rams` is on by default; with `false` nobody is credited."""
    assert ReplayRules().toggles.credit_rams is True
    sc = _two_players()
    _collision(sc)
    result = sc.result(ReplayRules(toggles=RuleToggles(credit_rams=False)))
    a, b = by_acct(result, 1), by_acct(result, 2)
    assert (a.kills_air, b.kills_air) == (0, 0)
    assert (a.loss_cause, b.loss_cause) == ("self", "self")
    assert (a.outcome, b.outcome) == ("crashed", "crashed")
    assert _detected(sc, ReplayRules()) == 1  # the signal is found either way; the toggle only decides about credit


def test_credit_rams_gives_each_pilot_a_kill_and_a_shot_down_loss() -> None:
    sc = _two_players()
    _collision(sc)
    result = sc.result(CREDIT)
    a, b = by_acct(result, 1), by_acct(result, 2)
    assert (a.kills_air, b.kills_air) == (1, 1)
    assert (a.kills_air_pvp, b.kills_air_pvp) == (1, 1)
    assert (a.loss_cause, b.loss_cause) == ("attacker", "attacker")
    assert (a.outcome, b.outcome) == ("shot_down", "shot_down")
    assert (a.is_death, b.is_death, a.is_plane_lost, b.is_plane_lost) == (True, True, True, True)
    assert sorted(_kills(result)) == [(a.index, b.index), (b.index, a.index)]
    assert all(k.via == "direct" and not k.is_friendly for k in result.kills)


def test_the_victim_timeline_names_the_rammer() -> None:
    sc = _two_players()
    _collision(sc)
    result = sc.result(CREDIT)
    a, b = by_acct(result, 1), by_acct(result, 2)
    (shot_down,) = [entry for entry in a.timeline if entry.kind == "shot_down"]
    assert shot_down.counterpart is not None
    assert (shot_down.counterpart.object_type, shot_down.counterpart.sortie_index) == ("MiG-15bis", b.index)


def test_collision_with_a_friend_credits_nobody() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.fly(500, 501, 3)  # a second F-86A of the same coalition
    sc.kill(110, NO, 100, pos=FAR)
    sc.kill(110.04, NO, 500, pos=BESIDE)
    result = sc.result(CREDIT)
    assert (by_acct(result, 1).kills_air, by_acct(result, 3).kills_air) == (0, 0)
    assert by_acct(result, 1).loss_cause == "self"
    assert _detected(sc) == 1  # still a ram, but between friends: no kill, and no friendly-kill penalty either


@pytest.mark.parametrize(
    ("gap", "apart"),
    [
        pytest.param(3.0, BESIDE, id="too-far-apart-in-time"),
        pytest.param(0.6, BESIDE, id="just-over-the-0.5-s-window"),
        pytest.param(0.04, Pos(FAR.x + 20.0, FAR.y, FAR.z), id="just-over-the-15-m-distance"),
        pytest.param(0.04, Pos(FAR.x + 80.0, FAR.y, FAR.z), id="too-far-apart-in-space"),
        pytest.param(0.04, Pos(FAR.x, FAR.y + 80.0, FAR.z), id="too-far-apart-in-height"),
    ],
)
def test_not_a_ram_when_not_close_enough(gap: float, apart: Pos) -> None:
    sc = _two_players()
    _collision(sc, gap=gap, apart=apart)
    assert _detected(sc) == 0
    assert by_acct(sc.result(CREDIT), 1).kills_air == 0


def test_the_thresholds_are_configurable() -> None:
    sc = _two_players()
    _collision(sc, gap=3.0)
    wide = ReplayRules(toggles=RuleToggles(credit_rams=True, ram_window_s=5.0))
    assert _detected(sc, wide) == 1
    assert by_acct(sc.result(wide), 1).kills_air == 1


def test_guns_between_the_two_rule_out_a_ram() -> None:
    sc = _two_players()
    sc.hit(109, 100, 200)  # A hit B, then both died: not a plain collision
    _collision(sc)
    assert _detected(sc) == 0


def test_a_victim_with_an_attacker_is_not_a_ram() -> None:
    """B was shot down by the AI MiG 300 at the same moment A died by the environment: A flew into the debris."""
    sc = _two_players()
    sc.declare(0, 300, "MiG-15bis", 501)
    sc.takeoff(5, 300)
    sc.kill(110, NO, 100, pos=FAR)
    sc.kill(110.04, 300, 200, pos=BESIDE)
    result = sc.result(CREDIT)
    assert by_acct(result, 1).kills_air == 0
    assert by_acct(result, 2).loss_cause == "attacker"  # the AI's kill, as without the toggle


def test_the_despawn_at_mission_end_is_no_ram() -> None:
    sc = _two_players()
    sc.mission_end(100)
    _collision(sc, t=100.5)
    assert _detected(sc) == 0


def test_aircraft_destroyed_on_the_ground_are_no_ram() -> None:
    sc = Scenario()
    sc.player(0, 100, 101, 1)
    sc.player(0, 200, 201, 2, aircraft_type="MiG-15bis", country=501)
    sc.kill(30, NO, 100, pos=FAR)
    sc.kill(30.04, NO, 200, pos=BESIDE)
    assert _detected(sc) == 0


def test_ramming_an_ai_aircraft_is_a_kill_and_an_ai_ram_credits_its_type() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.declare(0, 300, "MiG-15bis", 501)
    sc.takeoff(5, 300)
    sc.kill(110, NO, 100, pos=FAR)
    sc.kill(110.04, NO, 300, pos=BESIDE)
    result = sc.result(CREDIT)
    a = by_acct(result, 1)
    assert (a.kills_air, a.kills_air_ai, a.kills_air_pvp) == (1, 1, 0)
    assert a.loss_cause == "attacker"
    killed_a = next(k for k in result.kills if k.victim_sortie_index == a.index)
    assert (killed_a.killer_sortie_index, killed_a.killer_type, killed_a.killer_coalition) == (None, "MiG-15bis", 1)
