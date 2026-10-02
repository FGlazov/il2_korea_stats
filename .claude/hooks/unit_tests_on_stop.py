"""Stop hook: run the fast unit tests before Claude finishes; on failure, block once and show the failures (exit 2)."""

import json
import subprocess
import sys


def main() -> int:
    payload = json.load(sys.stdin)
    if payload.get("stop_hook_active"):
        return 0  # already blocked once this turn; don't loop
    result = subprocess.run(
        ["uv", "run", "pytest", "tests/unit", "-q", "-x"], capture_output=True, text=True, check=False
    )
    if result.returncode not in (0, 5):  # 5 = no tests collected
        print("Unit tests fail:\n" + result.stdout[-4000:], file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
