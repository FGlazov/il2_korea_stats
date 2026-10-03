"""Distribution checks over real missions (opt-in, `-m sample_data`; doc 08, doc 12 expected numbers)."""

from collections import Counter

import pytest

from il2ks.core.catalog.loader import load_default_catalog
from il2ks.core.logparse.files import group_mission_files, parse_mission
from il2ks.core.logparse.parser import ParseStats
from il2ks.core.replay.state import run
from tests.conftest import SAMPLE_DATA

STRIDE = 7  # every 7th mission keeps the run to about 20 s


@pytest.mark.sample_data
def test_sample_distributions_stay_in_expected_bands() -> None:
    logs = group_mission_files((SAMPLE_DATA / "2026-09").iterdir(), txt_as="archive")[::STRIDE]
    catalog = load_default_catalog()
    counts = Counter[str]()
    for log in logs:
        result = run(parse_mission(log, ParseStats()), catalog)
        assert not result.unknown_object_types
        for sortie in result.sorties:
            if sortie.role != "pilot":
                continue
            counts["pilot"] += 1
            counts["mission_ended"] += sortie.ended_by_mission_end and sortie.takeoff_tick is not None
            if sortie.takeoff_tick is None:
                continue
            counts["took_off"] += 1
            counts["bailout"] += sortie.pilot_fate == "bailed_out"
            counts["early"] += sortie.suspected_early_bailout
            counts["structural"] += sortie.suspected_structural_failure
            counts["attacker"] += sortie.loss_cause == "attacker"
            counts["self"] += sortie.loss_cause == "self"
            counts["unknown_fate"] += sortie.pilot_fate == "unknown"
    took_off = counts["took_off"]
    assert 0.10 <= counts["bailout"] / took_off <= 0.15  # doc 12: 12.6%
    assert 0.02 <= counts["early"] / took_off <= 0.04  # doc 12: 2.7%
    assert 0.06 <= counts["mission_ended"] / counts["pilot"] <= 0.12  # doc 12: about 10%
    lost = counts["attacker"] + counts["self"]
    assert 0.4 <= counts["attacker"] / lost <= 0.6  # FR-ING-17: about 50/50
    assert counts["structural"] / lost == pytest.approx(0.06, abs=0.02)  # 391 of 6,367
    assert counts["unknown_fate"] / took_off < 0.01
