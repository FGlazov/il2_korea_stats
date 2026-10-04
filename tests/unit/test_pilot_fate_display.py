"""The displayed pilot fate (maintainer request 2026-10-04): dead > captured > survived, doc 13 `pilot_status`."""

import pytest

from il2ks.db.models import PlayerSortie
from il2ks.web import display


def fate_of(**fields: object) -> PlayerSortie:
    defaults: dict[str, object] = {
        "pilot_fate": "in_aircraft",
        "is_death": False,
        "is_captured": False,
        "pilot_status": "healthy",
    }
    return PlayerSortie(**{**defaults, **fields})


@pytest.mark.parametrize(
    ("fields", "key"),
    [
        ({}, "survived"),
        ({"pilot_fate": "unknown"}, "survived"),  # unknown is shown as Survived
        ({"pilot_fate": "bailed_out", "pilot_status": "wounded"}, "survived"),
        ({"pilot_fate": "disconnected"}, "survived"),
        ({"is_death": True}, "dead"),
        ({"is_death": True, "pilot_fate": "bailed_out"}, "dead"),  # dead beats the detailed fate
        ({"pilot_status": "dead"}, "dead"),
        ({"is_captured": True, "pilot_fate": "bailed_out"}, "captured"),
        ({"is_captured": True, "pilot_fate": "exited_on_ground"}, "captured"),
        ({"pilot_status": "captured"}, "captured"),
        ({"is_death": True, "is_captured": True}, "dead"),  # dead > captured (doc 13)
    ],
)
def test_displayed_pilot_fate_precedence(fields: dict[str, object], key: str) -> None:
    assert display.pilot_fate_key(fate_of(**fields)) == key


def test_pilot_fate_badges_and_secondary_detail() -> None:
    assert display.badge_spec(display.PILOT_FATES, "dead")[:2] == ("Dead", "red")
    assert display.badge_spec(display.PILOT_FATES, "captured")[:2] == ("Captured", "orange")
    assert display.badge_spec(display.PILOT_FATES, "survived")[:2] == ("Survived", "green")
    assert display.pilot_fate_detail(fate_of(pilot_fate="bailed_out")) == "bailed out"
    assert display.pilot_fate_detail(fate_of(pilot_fate="exited_on_ground")) == "exited on ground"
    assert display.pilot_fate_detail(fate_of(pilot_fate="disconnected")) == "left the server"
    assert display.pilot_fate_detail(fate_of(pilot_fate="in_aircraft")) == ""
    assert display.pilot_fate_detail(fate_of(pilot_fate="unknown")) == ""
