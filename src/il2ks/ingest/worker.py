"""Parse + replay in a worker process for `il2ks reprocess` (NFR-PERF-4).

Imports only the core (no Django), because worker processes are spawned fresh on Windows and never run `django.setup()`.
"""

from __future__ import annotations

import os
import sys
from functools import cache
from pathlib import Path

from il2ks.core.catalog.loader import Catalog, load_default_catalog
from il2ks.core.logparse.files import MissionLog, parse_mission
from il2ks.core.logparse.parser import ParseStats
from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.result import MissionResult
from il2ks.core.replay.state import run as replay_run


@cache
def _catalog() -> Catalog:
    """Once per worker process."""
    return load_default_catalog()


def parse_and_replay(mission_uid: str, archive: Path, rules: ReplayRules) -> tuple[MissionResult, ParseStats]:
    stats = ParseStats()
    events = parse_mission(MissionLog(mission_uid, "archive", (archive,)), stats)
    return replay_run(events, _catalog(), rules), stats


def lower_priority() -> None:
    """Executor initializer: run workers below normal priority so DServer isn't affected (NFR-PERF-4)."""
    try:
        if sys.platform == "win32":
            import ctypes

            below_normal = 0x00004000
            kernel32 = ctypes.windll.kernel32
            kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), below_normal)
        else:
            os.nice(10)
    except OSError:
        pass  # best effort
