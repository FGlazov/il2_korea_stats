"""PostToolUse hook: format and lint an edited Python file; report leftover lint errors back to Claude (exit 2)."""

import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    payload = json.load(sys.stdin)
    file_path = payload.get("tool_input", {}).get("file_path", "")
    if not file_path.endswith(".py") or not Path(file_path).exists():
        return 0
    subprocess.run(["uv", "run", "ruff", "format", "--quiet", file_path], check=False)
    result = subprocess.run(
        ["uv", "run", "ruff", "check", "--fix", "--quiet", file_path], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        print(result.stdout + result.stderr, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
