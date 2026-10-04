"""One command for a local load test: seed a scratch database, start `il2ks web --dev`, run Locust, stop the server.

    uv run python loadtest/run.py                    # 20 users for 60 s on a fresh seeded database
    uv run python loadtest/run.py -u 50 -t 120s      # more users, longer
    uv run python loadtest/run.py --data-dir D       # reuse a seeded data dir (skips the slow seeding)
    uv run python loadtest/run.py --html report.html # also write Locust's HTML report

The report table (RPS, p50, p95 per endpoint) is printed at the end; the exit code is 1 when more than 1% of the
requests failed. Never touches your real data dir: it uses its own scratch one (docs/performance-testing.md)."""

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def child_env(data_dir: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("IL2KS_", "DJANGO_"))}
    env.update(
        IL2KS_DATA_DIR=str(data_dir),
        DJANGO_SETTINGS_MODULE="il2ks.settings",
        PYTHONPATH=str(REPO),
        PYTHONIOENCODING="utf-8",
    )
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-u", "--users", default="20", help="concurrent users (default 20)")
    parser.add_argument("-r", "--spawn-rate", default="10", help="users started per second (default 10)")
    parser.add_argument("-t", "--run-time", default="60s", help="how long to run (default 60s)")
    parser.add_argument("--missions", default="60", help="missions to seed (default 60)")
    parser.add_argument("--data-dir", type=Path, help="a data dir seeded before; skips seeding")
    parser.add_argument("--html", help="write Locust's HTML report to this file")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as scratch:
        data_dir = args.data_dir or Path(scratch) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        env = child_env(data_dir)
        if args.data_dir is None:
            print(f"seeding {args.missions} missions into {data_dir} (about a minute)...", flush=True)
            subprocess.run([sys.executable, "-m", "tests.perf.seed", args.missions], cwd=REPO, env=env, check=True)
        port = free_port()
        host = f"http://127.0.0.1:{port}"
        server = subprocess.Popen(
            [sys.executable, "-m", "il2ks.cli", "web", "--dev", "--host", "127.0.0.1", "--port", str(port)],
            cwd=scratch,
            env=env,
        )
        try:
            deadline = time.monotonic() + 90
            while True:
                try:
                    with urllib.request.urlopen(host + "/", timeout=2):
                        break
                except (urllib.error.URLError, OSError):
                    if server.poll() is not None or time.monotonic() > deadline:
                        print("the server did not start", file=sys.stderr)
                        return 2
                    time.sleep(0.5)
            locust = [
                sys.executable,
                "-m",
                "locust",
                "-f",
                str(REPO / "loadtest" / "locustfile.py"),
                "--host",
                host,
                "--headless",
                "-u",
                args.users,
                "-r",
                args.spawn_rate,
                "-t",
                args.run_time,
                "--only-summary",
            ]
            if args.html:
                locust += ["--html", args.html]
            return subprocess.run(locust, cwd=REPO, env={**os.environ, "PYTHONPATH": str(REPO)}).returncode
        finally:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    sys.exit(main())
