"""Production static collection minifies our own CSS and JS before hashing (doc 16); the sources stay readable."""

import json
import shutil
from pathlib import Path

import pytest
from django.contrib.staticfiles import finders
from django.core.management import call_command
from pytest_django.fixtures import Settings

from il2ks.serving.storage import minifier_for

WEB_STATIC = Path(__file__).resolve().parents[2] / "src" / "il2ks" / "web" / "static"
OVERRIDE_HEADER = "/*! il2ks-template: static/il2ks/site.css v1 */\n"


@pytest.fixture
def collected(settings: Settings, tmp_path: Path) -> Path:
    custom = tmp_path / "custom_static" / "il2ks"
    custom.mkdir(parents=True)
    (custom / "mine.css").write_text(OVERRIDE_HEADER + "body {\n    color : red;\n}\n", encoding="utf-8")
    root = tmp_path / "staticfiles"
    settings.STATIC_ROOT = str(root)
    settings.STATICFILES_DIRS = [str(tmp_path / "custom_static")]
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "il2ks.serving.storage.LenientManifestStorage"},
    }
    call_command("collectstatic", interactive=False, verbosity=0)
    return root


def _manifest(root: Path) -> dict[str, str]:
    paths = json.loads((root / "staticfiles.json").read_text(encoding="utf-8"))["paths"]
    return {name: hashed.replace("\\", "/") for name, hashed in paths.items()}  # Windows writes backslashes


@pytest.mark.parametrize("name", ["il2ks/il2ks.js", "il2ks/sortie.js", "il2ks/site.css", "il2ks/sorties.css"])
def test_own_files_are_smaller_and_hashed_by_the_minified_content(collected: Path, name: str) -> None:
    source = (WEB_STATIC / name).read_bytes()
    hashed = collected / _manifest(collected)[name]
    assert hashed.stat().st_size < len(source) * 0.9
    assert b"\n\n" not in hashed.read_bytes()
    # the manifest names the file after the minified bytes: hashing the readable source would give another name
    assert _manifest(collected)[name] != name
    if name.endswith(".js"):  # (a stylesheet is also rewritten for the hashed names of its url()s)
        assert (collected / name).read_bytes() == hashed.read_bytes()


def test_vendored_and_admin_files_are_untouched(collected: Path) -> None:
    for name in ["il2ks/vendor/htmx.min.js", "il2ks/vendor/pico.min.css"]:
        assert (collected / _manifest(collected)[name]).read_bytes() == (WEB_STATIC / "il2ks" / name[6:]).read_bytes()
    admin = finders.find("admin/js/core.js")
    assert isinstance(admin, str)
    assert (collected / _manifest(collected)["admin/js/core.js"]).read_bytes() == Path(admin).read_bytes()


def test_the_repo_sources_stay_readable(collected: Path) -> None:
    assert b"\n    " in (WEB_STATIC / "il2ks" / "site.css").read_bytes()


def test_an_override_in_custom_static_is_minified_and_keeps_its_marker_comment(collected: Path) -> None:
    text = (collected / _manifest(collected)["il2ks/mine.css"]).read_text(encoding="utf-8")
    assert text.startswith("/*! il2ks-template: static/il2ks/site.css v1 */")
    assert "body{color:red}" in text


def test_collecting_twice_is_stable(collected: Path, settings: Settings) -> None:
    before = _manifest(collected)
    call_command("collectstatic", interactive=False, verbosity=0)
    assert _manifest(collected) == before
    shutil.rmtree(collected / "il2ks", ignore_errors=True)  # a half-cleaned root is refilled by the next collect
    call_command("collectstatic", interactive=False, verbosity=0)
    assert _manifest(collected) == before


@pytest.mark.parametrize(
    ("name", "minified"),
    [
        ("il2ks/a.js", True),
        ("il2ks/a.css", True),
        ("il2ks/vendor/a.js", False),
        ("il2ks/a.min.js", False),
        ("admin/js/a.js", False),
        ("il2ks/a.svg", False),
        ("il2ks\\vendor\\a.js", False),
        ("admin\\js\\a.js", False),
        ("il2ks\\a.js", True),
    ],
)
def test_which_files_are_minified(name: str, minified: bool) -> None:
    assert (minifier_for(name) is not None) is minified


def test_javascript_keeps_license_comments_and_strings() -> None:
    minify = minifier_for("a.js")
    assert minify is not None
    out = minify("/*! (c) me */\n// gone\nvar a = `x  y ${ 1 + 2 }`;\nvar b = 'p  q'; /* gone */\n")
    assert out.startswith("/*! (c) me */")
    assert "gone" not in out
    assert "`x  y ${ 1 + 2 }`" in out
    assert "'p  q'" in out
