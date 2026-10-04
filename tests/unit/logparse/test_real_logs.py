"""Whole missions: the anonymized fixtures, and opt-in the real `sample_data/` (doc 08)."""

from collections import Counter
from pathlib import Path

import pytest

from il2ks.core.logparse.events import LogEvent
from il2ks.core.logparse.files import MissionLog, group_mission_files, parse_mission
from il2ks.core.logparse.parser import ParseStats
from tests.conftest import FIXTURE_LOGS, SAMPLE_DATA

FIXTURES = sorted(FIXTURE_LOGS.glob("*.txt.zip"))


def parse_counts(log: MissionLog) -> tuple[ParseStats, Counter[str]]:
    stats = ParseStats()
    counts = Counter[str]()
    event: LogEvent
    for event in parse_mission(log, stats):
        counts[type(event).__name__] += 1
    return stats, counts


def check_mission(log: MissionLog) -> tuple[ParseStats, Counter[str]]:
    """Every line parses, nothing unexpected, and the event mix looks like a mission."""
    stats, counts = parse_counts(log)
    assert stats.lines_bad == 0, stats.warnings[:5]
    assert stats.lines_total == sum(counts.values())
    assert stats.log_version == 18
    assert not stats.unknown_keys
    assert not stats.unknown_atypes
    assert counts["MissionStartEvent"] == 1
    assert counts["LogVersionEvent"] >= 1
    return stats, counts


def test_all_fixtures_present() -> None:
    assert len(FIXTURES) == 5


@pytest.mark.parametrize("path", FIXTURES, ids=[p.name for p in FIXTURES])
def test_fixture_parses_without_bad_lines(path: Path) -> None:
    check_mission(MissionLog("fixture", "archive", (path,)))


def check_sample_dir(root: Path, limit: int) -> None:
    logs = group_mission_files(root.iterdir(), txt_as="archive")
    assert logs
    total = Counter[str]()
    for log in logs[:limit]:
        _, counts = check_mission(log)
        # Some missions are short (a crashed or restarted server), so per-mission ratios aren't stable.
        assert counts["PlayerSpawnEvent"] > 0
        assert counts["SortieEndEvent"] > 0
        assert counts["ObjectSpawnEvent"] > counts["PlayerSpawnEvent"]
        total.update(counts)
    assert total["HitEvent"] > total["DamageEvent"] > 0


@pytest.mark.sample_data
def test_sample_missions_parse_without_bad_lines() -> None:
    check_sample_dir(SAMPLE_DATA / "2026-09", limit=5)
