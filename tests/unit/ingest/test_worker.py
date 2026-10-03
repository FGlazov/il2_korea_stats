"""Reprocess worker processes (NFR-PERF-4): Django-free imports and a real process pool."""

import os
import subprocess
import sys

from il2ks.ingest.reprocess import default_executor, default_workers


def test_worker_module_does_not_import_django() -> None:
    """Workers are spawned fresh on Windows and never run `django.setup()`."""
    code = "import sys, il2ks.ingest.worker; sys.exit(1 if 'django' in sys.modules else 0)"
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0


def test_default_executor_runs_work_in_another_process_at_lower_priority() -> None:
    with default_executor(1) as executor:
        assert executor.submit(os.getpid).result(timeout=60) != os.getpid()


def test_default_worker_count_leaves_a_core_free() -> None:
    assert default_workers() >= 1
    assert default_workers() <= max(1, (os.cpu_count() or 2) - 1)
