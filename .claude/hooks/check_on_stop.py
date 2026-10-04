"""Stop hook: run the fast tier of `il2ks dev check` before Claude finishes (lint, types, migration / template /
translation guards, guard tests; ~15-30 s). Skipped when nothing changed since the last passing run, so turns that only
talk cost nothing. On failure it blocks once (exit 2) and shows what failed and how to fix it. The full pre-commit
tier (`il2ks dev check`, with all unit tests) is the agent's own job before committing; pre-commit enforces it."""

import json
import subprocess
import sys

LIMIT = 6000


def main() -> int:
    payload = json.load(sys.stdin)
    if payload.get("stop_hook_active"):
        return 0  # already blocked once this turn; don't loop
    result = subprocess.run(
        [sys.executable, "-m", "il2ks", "dev", "check", "--fast", "--if-changed"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        print("`il2ks dev check --fast` failed:\n" + (result.stdout + result.stderr)[-LIMIT:], file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
