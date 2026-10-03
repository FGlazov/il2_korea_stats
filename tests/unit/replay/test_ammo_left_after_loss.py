"""AType 4 ammo left is empty for a destroyed aircraft that the pilot left (research on the 210 samples: with the sortie
end within 1 s of the destruction, 90% of unfired stores read as still loaded; with a bailout, 100% read as 0), so
ammo used is not derived from it."""

import pytest

from il2ks.core.replay.config import ReplayRules
from tests.unit.replay.builder import NO, Scenario, by_acct


def lost_after(seconds: float, rules: ReplayRules | None = None) -> bool:
    sc = Scenario()
    sc.fly_a()
    sc.damage(100, NO, 100, 1.0)
    sc.kill(100, NO, 100)
    sc.end(100 + seconds, 100, 101)
    a = by_acct(sc.result(rules), 1)
    assert a.is_plane_lost
    return a.ammo_left_after_loss


def test_sortie_ending_with_the_destruction_has_a_trustworthy_ammo_left() -> None:
    assert lost_after(0.1) is False
    assert lost_after(1.0) is False


def test_sortie_ending_later_than_the_threshold_after_the_destruction_does_not() -> None:
    assert lost_after(1.5) is True
    assert lost_after(25.0) is True


def test_threshold_is_a_rule() -> None:
    assert lost_after(1.5, ReplayRules(ammo_left_after_loss_s=2.0)) is False
    assert ReplayRules().ammo_left_after_loss_s == pytest.approx(1.0)


def test_sortie_ended_before_the_destruction_or_not_lost_is_trustworthy() -> None:
    sc = Scenario()
    sc.fly_a()
    sc.end(100, 100, 101)
    sc.kill(101, NO, 100)  # the shot-down shape: AType 4 first
    assert by_acct(sc.result(), 1).ammo_left_after_loss is False
    sc = Scenario()
    sc.fly_a()
    sc.end(300, 100, 101)
    assert by_acct(sc.result(), 1).ammo_left_after_loss is False
