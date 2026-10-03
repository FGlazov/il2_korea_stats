"""Catalog coverage on real logs (opt-in: needs the gitignored `sample_data/`, doc 08).

A plain regex scan, independent of `core.logparse`: AType 12 `TYPE` runs until ` COUNTRY:` (names can contain spaces
and commas, TD-20); AType 10 carries `TYPE` and `PAYLOAD`.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from il2ks.core.catalog.loader import Catalog, load_default_catalog
from tests.conftest import SAMPLE_DATA

OBJECT_SPAWN = re.compile(r"AType:12 ID:-?\d+ TYPE:(.*?) COUNTRY:")
PLAYER_SPAWN = re.compile(r"AType:10 .*? TYPE:(.*?) COUNTRY:-?\d+ .*? PAYLOAD:(-?\d+) ")


@dataclass
class Scan:
    object_types: Counter[str] = field(default_factory=Counter[str])
    player_types: Counter[str] = field(default_factory=Counter[str])
    spawns: int = 0
    resolved: int = 0
    unresolved: Counter[tuple[str, int]] = field(default_factory=Counter[tuple[str, int]])


def sample_missions(root: Path, every: int = 1) -> list[Path]:
    """Every `every`-th whole-mission log (all by default: about 6 s for 210 missions)."""
    return sorted(root.glob("*/*.txt"))[::every]


def scan(paths: list[Path], catalog: Catalog) -> Scan:
    result = Scan()
    for path in paths:
        with path.open(encoding="latin-1") as f:
            for line in f:
                if "AType:12 " in line:
                    if m := OBJECT_SPAWN.search(line):
                        result.object_types[m.group(1)] += 1
                elif "AType:10 " in line and (m := PLAYER_SPAWN.search(line)):
                    aircraft, payload_id = m.group(1), int(m.group(2))
                    result.player_types[aircraft] += 1
                    result.spawns += 1
                    if catalog.payload(aircraft, payload_id) is not None:
                        result.resolved += 1
                    else:
                        result.unresolved[(aircraft, payload_id)] += 1
    return result


def check(result: Scan, catalog: Catalog) -> None:
    unknown = {t for t in result.object_types if not catalog.lookup(t).is_known}
    assert not unknown, f"object types missing from objects.csv: {sorted(unknown)[:50]}"
    unknown_players = {t for t in result.player_types if not catalog.lookup(t).is_known}
    assert not unknown_players
    assert all(catalog.lookup(t).is_playable for t in result.player_types)
    assert result.spawns > 0
    pilot_spawns = result.spawns - sum(n for t, n in result.player_types.items() if catalog.lookup(t).cls == "gunner")
    assert result.resolved / pilot_spawns >= 0.99, result.unresolved.most_common(10)
    assert result.resolved / result.spawns >= 0.99, result.unresolved.most_common(10)


@pytest.mark.sample_data
def test_sample_missions_fully_covered() -> None:
    paths = sample_missions(SAMPLE_DATA)
    if not paths:
        pytest.skip("no whole-mission .txt logs in sample_data/")
    catalog = load_default_catalog()
    check(scan(paths, catalog), catalog)
