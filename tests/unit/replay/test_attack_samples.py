"""Combat role and time on target over real missions (opt-in, `-m sample_data`; doc 13, FR-WEB-19/20)."""

from collections import Counter

import pytest

from il2ks.core.catalog.loader import load_default_catalog
from il2ks.core.logparse.files import group_mission_files, parse_mission
from il2ks.core.logparse.parser import ParseStats
from il2ks.core.replay.state import run
from tests.conftest import SAMPLE_DATA

STRIDE = 7


@pytest.mark.sample_data
def test_roles_and_time_on_target_stay_in_expected_bands() -> None:
    logs = group_mission_files((SAMPLE_DATA / "2026-09").iterdir(), txt_as="archive")[::STRIDE]
    catalog = load_default_catalog()
    roles = Counter[tuple[str, str | None]]()
    zero = flown = 0
    for log in logs:
        for sortie in run(parse_mission(log, ParseStats()), catalog).sorties:
            roles[(sortie.aircraft_type, sortie.combat_role)] += 1
            assert (sortie.time_on_target_s is not None) == (sortie.combat_role == "attack")
            if sortie.combat_role == "attack" and sortie.takeoff_tick is not None:
                flown += 1
                zero += sortie.time_on_target_s == 0.0
    assert all(role is None for (kind, role) in roles if kind.startswith("Turret"))
    assert not roles[("IL-10", "air_superiority")]
    assert roles[("IL-10", "attack")]
    for prop_fighter in ("La-11", "Yak-9P"):
        assert not roles[(prop_fighter, "attack")]
    assert 0.25 <= zero / flown <= 0.50  # measured on all 210 missions: 37.5% of attack sorties that took off
