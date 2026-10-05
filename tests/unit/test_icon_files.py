"""Built-in icon files: referenced names exist; icons follow the Tabler-style contract (OQ-37, doc 15)."""

# ruff: noqa: SIM905
import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "src" / "il2ks" / "web"
IMG = WEB / "static" / "il2ks" / "img"
GROUPS = "aircraft|brand|coalition|event|ground|outcome|role|stat"
REFERENCE = re.compile(rf"""["']((?:{GROUPS})/[a-z0-9][a-z0-9_/-]*)["']""")
# The file names in doc 15's asset list that exist as icons today (the contract for custom/static overrides).
CONTRACT = {
    *(
        f"event/{n}"
        for n in "spawn takeoff landing kill-air kill-ground assist friendly-fire damaged destroyed bailout "
        "disconnect sortie-end bomb-release rocket-salvo ram".split()
    ),
    *(
        f"outcome/{n}"
        for n in "landed ditched crashed shot-down in-flight not-taken-off mission-ended unknown bailed-out "
        "exited-on-ground disconnected captured wounded dead".split()
    ),
    *(f"ground/{n}" for n in "tank vehicle artillery aaa ship train building parked-aircraft other-static".split()),
    *(
        f"stat/{n}"
        for n in "sorties flight-time air-kills ground-kills assists deaths planes-lost bailouts captures "
        "taxi-accidents strafed".split()
    ),
    "role/air-superiority",
    "role/attack",
    "coalition/redfor",
    "coalition/blufor",
}


def referenced_names() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in [*(WEB / "templates").rglob("*.html"), *WEB.glob("*.py"), *(WEB / "templatetags").glob("*.py")]:
        for match in REFERENCE.finditer(path.read_text(encoding="utf-8")):
            found.setdefault(match.group(1), []).append(path.name)
    return found


def test_every_referenced_icon_exists() -> None:
    """Names in templates (`{% icon "stat/sorties" %}`, `icon="..."`) and in web/*.py icon maps point at real files."""
    missing = {name: files for name, files in referenced_names().items() if not (IMG / f"{name}.svg").is_file()}
    assert not missing, f"icons referenced but missing under static/il2ks/img/: {missing}"


def test_contract_names_exist() -> None:
    missing = sorted(name for name in CONTRACT if not (IMG / f"{name}.svg").is_file())
    assert not missing, f"doc 15 file names with no file: {missing}"


@pytest.mark.parametrize("group", ["event", "outcome", "ground", "stat", "role", "coalition", "nav"])
def test_icons_are_currentcolor_24px_svgs(group: str) -> None:
    files = [path for path in (IMG / group).glob("*.svg")]
    assert files
    for path in files:
        svg = path.read_text(encoding="utf-8")
        assert svg.lstrip().startswith("<svg"), path
        assert 'viewBox="0 0 24 24"' in svg, path
        assert 'stroke="currentColor"' in svg, path
        assert "class=" not in svg, f"{path}: the icon tag adds the class"
        assert not re.search(r"#[0-9a-fA-F]{3,6}\b", svg), f"{path}: hard-coded colour"


def test_every_navigation_link_icon_has_a_file() -> None:
    """The admin's choice list for custom navigation links (`NavIcon`) and `static/il2ks/img/nav/` agree."""
    from il2ks.db.models import NavIcon

    assert {value for value in NavIcon.values if value} == {path.stem for path in (IMG / "nav").glob("*.svg")}
