"""The release workflow (`.github/workflows/release.yml`) as text: it only runs on a version tag, so a mistake shows up
when a release is already tagged (v0.1.0: the Windows smoke job failed and nothing was published)."""

import re
from pathlib import Path

RELEASE = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"


def test_the_smoke_venv_is_named_by_its_folder() -> None:
    """`uv pip install --python smoke-venv/Scripts/python` fails on Windows ("No virtual environment or system Python
    installation found": uv does not add `.exe`). The venv's folder works on every system."""
    text = RELEASE.read_text(encoding="utf-8")
    installs = re.findall(r"uv pip install --python (\S+)", text)
    assert installs, "the smoke job installs the wheel with uv pip"
    assert all(target == "smoke-venv" for target in installs), installs
