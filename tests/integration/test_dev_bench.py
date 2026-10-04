"""`il2ks dev bench-ingest` and `dev dump-db` (docs/performance-testing.md): a smoke test on the small fixture logs.

They run in a child process: the benchmark points `IL2KS_DATA_DIR` at a throw-away folder and calls `django.setup()`,
which must not leak into the test process."""

import shutil
import subprocess
import sys
from pathlib import Path

from tests.conftest import FIXTURE_LOGS

FIXTURES = {"typical": "2026-09-01_10-00-00", "most_bailouts": "2026-09-02_10-00-00"}


def logs_dir(tmp_path: Path) -> Path:
    """The fixtures under DServer-style names (so grouping recognizes them), in a temp folder."""
    source = tmp_path / "logs"
    source.mkdir()
    for name, uid in FIXTURES.items():
        shutil.copy(FIXTURE_LOGS / f"{name}.txt.zip", source / f"missionReport({uid})[0].txt.zip")
    return source


def run_dev(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "il2ks", "dev", *args], capture_output=True, text=True, timeout=300, check=False
    )


def test_bench_ingest_times_the_phases_and_dump_db_reads_the_result(tmp_path: Path) -> None:
    data = tmp_path / "data"
    done = run_dev("bench-ingest", str(logs_dir(tmp_path)), "--limit", "2", "--cpu", "--data-dir", str(data))
    assert done.returncode == 0, done.stdout + done.stderr
    assert "ingested 2" in done.stdout
    for phase in ("parse", "replay", "persist L1"):
        assert phase in done.stdout
    assert any(data.iterdir())

    target = tmp_path / "dump.jsonl"
    dumped = run_dev("dump-db", str(data), str(target))
    assert dumped.returncode == 0, dumped.stdout + dumped.stderr
    assert target.read_text(encoding="utf-8").strip()


def test_bench_ingest_works_in_a_throw_away_data_dir(tmp_path: Path) -> None:
    done = run_dev("bench-ingest", str(logs_dir(tmp_path)), "--limit", "1")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "ingested 1" in done.stdout
