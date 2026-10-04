"""`il2ks dev bench-ingest <logs dir>`: time a full ingest of a log folder, split by phase.

Imports the folder (`ingest --from` semantics) into a throw-away data dir and prints the total, the per-mission median
and p95, and the share of each phase: archive, parse, replay, persist level 1, level-2 recompute, Elo ratings and the
rest (transaction, `IngestRun`, commit). Parse and replay are normally one lazy pipeline; here the parser's events are
collected first so the two can be timed apart (this costs memory, so the totals are an upper bound). Run it before and
after a performance change and compare. `--profile FILE` also writes a cProfile dump of the whole run.
"""

import contextlib
import cProfile
import os
import shutil
import statistics
import tempfile
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path

_clock: Callable[[], float] = time.perf_counter
"""Wall clock; `--cpu` swaps in the process CPU time, which other load on the machine (antivirus, other jobs) does not
inflate. Windows resolves CPU time to ~15 ms, fine for the sums and medians below but not for single small phases."""

PHASES = ("archive", "parse", "replay", "persist L1", "level 2", "ratings", "other")


class PhaseTimer:
    """Accumulates seconds per phase for the mission being ingested and keeps the finished missions."""

    def __init__(self) -> None:
        self.current: defaultdict[str, float] = defaultdict(float)
        self.missions: list[tuple[float, dict[str, float]]] = []

    def wrap[**P, R](self, phase: str, fn: Callable[P, R]) -> Callable[P, R]:
        def timed(*args: P.args, **kwargs: P.kwargs) -> R:
            t0 = _clock()
            try:
                return fn(*args, **kwargs)
            finally:
                self.current[phase] += _clock() - t0

        return timed

    def finish(self, total: float) -> None:
        phases = dict(self.current)
        self.current.clear()
        # `persist L1` wrapped all of save_mission: take the level-2 and ratings time that ran inside it back out.
        phases["persist L1"] = phases.get("persist L1", 0.0) - phases.get("level 2", 0.0) - phases.get("ratings", 0.0)
        phases["other"] = total - sum(phases.values())
        self.missions.append((total, phases))


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def format_report(timer: PhaseTimer, wall: float) -> str:
    missions = timer.missions
    if not missions:
        return "no missions were ingested"
    totals = [t for t, _ in missions]
    grand = sum(totals)
    lines = [
        f"missions: {len(missions)}, wall time {wall:.1f} s, sum of missions {grand:.1f} s",
        f"per mission: median {statistics.median(totals) * 1000:.0f} ms, p95 {percentile(totals, 0.95) * 1000:.0f} ms, "
        f"max {max(totals) * 1000:.0f} ms",
        "",
        f"{'phase':<12}{'total s':>9}{'share':>8}{'median ms':>11}{'p95 ms':>9}",
    ]
    for phase in PHASES:
        values = [phases.get(phase, 0.0) for _, phases in missions]
        lines.append(
            f"{phase:<12}{sum(values):>9.2f}{sum(values) / grand:>8.1%}"
            f"{statistics.median(values) * 1000:>11.1f}{percentile(values, 0.95) * 1000:>9.1f}"
        )
    return "\n".join(lines)


def bench_ingest(
    source: Path,
    *,
    limit: int | None = None,
    profile: Path | None = None,
    data_dir: Path | None = None,
    cpu: bool = False,
) -> int:
    """Run the benchmark. Returns the process exit code. `data_dir` keeps the result (for `dump-db`); it must be
    empty or new. `cpu`: time CPU instead of wall clock."""
    global _clock
    _clock = time.process_time if cpu else time.perf_counter
    with contextlib.ExitStack() as stack:
        if data_dir is None:
            tmp = stack.enter_context(tempfile.TemporaryDirectory(prefix="il2ks-bench-"))
        else:
            data_dir.mkdir(parents=True, exist_ok=True)
            tmp = str(data_dir)
        os.environ["IL2KS_DATA_DIR"] = tmp
        os.environ.pop("IL2KS_CONFIG", None)
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "il2ks.settings")
        import django

        django.setup()
        from django.core.management import call_command
        from django.db import connections

        call_command("migrate", verbosity=0)

        from il2ks.config import load_config
        from il2ks.core.logparse.events import LogEvent
        from il2ks.core.logparse.files import MissionLog, MissionLogKind
        from il2ks.core.logparse.parser import ParseStats
        from il2ks.db.models import IngestRun
        from il2ks.ingest import persist, runner
        from il2ks.ingest.discover import Decision, Found

        cfg = load_config(None, {"IL2KS_DATA_DIR": tmp})
        # Always work on a copy of the input, so the benchmark can never move, delete or lock the originals.
        copy = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="il2ks-bench-input-")))
        for file in sorted(source.iterdir()):
            if file.is_file() and ".txt" in file.name:
                shutil.copy2(file, copy / file.name)
        source = copy
        timer = PhaseTimer()
        real = runner.default_pipeline(cfg)

        def group(paths: Iterable[Path], txt_as: MissionLogKind) -> list[MissionLog]:
            logs = real.group(paths, txt_as)
            return logs if limit is None else logs[:limit]

        def parse(log: MissionLog, stats: ParseStats) -> Iterable[LogEvent]:
            t0 = _clock()
            events = list(real.parse(log, stats))
            timer.current["parse"] += _clock() - t0
            return events

        pipeline = runner.Pipeline(
            group=group,
            parse=parse,
            replay=timer.wrap("replay", real.replay),
            save=timer.wrap("persist L1", real.save),
            resolve_start=real.resolve_start,
        )

        original_ingest_mission = runner.ingest_mission

        def ingest_mission(
            cfg: runner.Config,
            pipeline: runner.Pipeline,
            item: Found,
            decision: Decision,
            last: IngestRun | None,
            *,
            now: Callable[[], datetime] = runner.utcnow,
        ) -> runner.Outcome:
            t0 = _clock()
            outcome = original_ingest_mission(cfg, pipeline, item, decision, last, now=now)
            timer.finish(_clock() - t0)
            return outcome

        runner.ingest_mission = ingest_mission
        runner.write_archive = timer.wrap("archive", runner.write_archive)
        persist.recompute_players = timer.wrap("level 2", persist.recompute_players)
        persist.recompute_aircraft_ammo = timer.wrap("level 2", persist.recompute_aircraft_ammo)
        persist.recompute_aircraft_stats = timer.wrap("level 2", persist.recompute_aircraft_stats)
        persist.recompute_matchups = timer.wrap("level 2", persist.recompute_matchups)
        persist.recompute_thresholds = timer.wrap("level 2", persist.recompute_thresholds)
        persist.recompute_ratings = timer.wrap("ratings", persist.recompute_ratings)

        profiler = cProfile.Profile() if profile is not None else None
        start = _clock()
        if profiler is not None:
            profiler.enable()
        summary = runner.ingest_once(cfg, pipeline, runner.IngestOptions(source=source))
        if profiler is not None:
            profiler.disable()
        wall = _clock() - start
        print(summary.describe())
        print(format_report(timer, wall))
        if profiler is not None and profile is not None:
            profiler.dump_stats(str(profile))
            print(f"cProfile data: {profile}")
        connections.close_all()
        return 1 if summary.failed else 0
