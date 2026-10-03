"""PyPI packaging (doc 07: published as `il2ks`): one version source, and the wheel carries every data file.

The wheel build needs network access for the build backend (hatchling), so it is opt-in: `IL2KS_TEST_BUILD=1 uv run
pytest tests/unit/test_packaging.py`. CI and the release workflow set it."""

import os
import re
import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path
from typing import cast

import pytest

from il2ks import __version__

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "il2ks"

MUST_SHIP = [
    "il2ks/data/il2ks.example.toml",
    "il2ks/core/catalog/data/objects.csv",
    "il2ks/core/catalog/data/payloads.csv",
    "il2ks/core/catalog/data/payload_aliases.csv",
    "il2ks/db/migrations/0001_initial.py",
    "il2ks/web/templates/il2ks/base.html",
    "il2ks/__main__.py",
    "il2ks/serving/caddy.py",
]


def pyproject(*keys: str) -> object:
    """A value from pyproject.toml by key path."""
    data: object = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    for key in keys:
        assert isinstance(data, dict)
        data = cast(dict[str, object], data)[key]
    return data


def data_files_in_source() -> set[str]:
    """Everything under src/il2ks that is not Python code or a cache: templates, static files, CSVs, TOML, locale."""
    found: set[str] = set()
    for path in SRC.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".py", ".pyc"}:
            found.add(path.relative_to(SRC.parent).as_posix())
    return found


def test_the_version_has_a_single_source() -> None:
    assert "version" not in cast(dict[str, object], pyproject("project"))  # not repeated in pyproject.toml
    assert pyproject("project", "dynamic") == ["version"]
    assert pyproject("tool", "hatch", "version", "path") == "src/il2ks/__init__.py"
    assert re.fullmatch(r"\d+\.\d+\.\d+([a-z0-9.+-]*)", __version__)


def test_the_wheel_config_keeps_static_files_and_translations_that_gitignore_would_drop() -> None:
    assert pyproject("tool", "hatch", "build", "targets", "wheel", "packages") == ["src/il2ks"]
    artifacts = pyproject("tool", "hatch", "build", "targets", "wheel", "artifacts")
    assert isinstance(artifacts, list)
    assert "src/il2ks/web/static/**" in artifacts
    assert "src/il2ks/**/*.mo" in artifacts


def test_the_files_the_wheel_must_ship_exist_in_the_source_tree() -> None:
    for name in MUST_SHIP:
        assert (SRC.parent / name).is_file(), name


def test_the_console_script_points_at_the_cli() -> None:
    assert pyproject("project", "scripts") == {"il2ks": "il2ks.cli:main"}


@pytest.mark.skipif(os.environ.get("IL2KS_TEST_BUILD") != "1", reason="set IL2KS_TEST_BUILD=1 to build the wheel")
def test_the_built_wheel_contains_every_data_file(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv, "uv is needed to build"
    subprocess.run([uv, "build", "--wheel", "--out-dir", str(tmp_path)], cwd=REPO, check=True)
    (wheel,) = tmp_path.glob("il2ks-*.whl")
    assert wheel.name.startswith(f"il2ks-{__version__}-")
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata = next(n for n in names if n.endswith(".dist-info/METADATA"))
        assert f"Version: {__version__}" in archive.read(metadata).decode("utf-8")
        entry_points = next(n for n in names if n.endswith(".dist-info/entry_points.txt"))
        assert "il2ks = il2ks.cli:main" in archive.read(entry_points).decode("utf-8")
    assert set(MUST_SHIP) <= names
    missing = data_files_in_source() - names
    assert not missing, f"not in the wheel: {sorted(missing)}"
    assert not [n for n in names if n.startswith(("tests/", "sample_data/", "design_doc/"))]
