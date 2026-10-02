import re
import zipfile
from pathlib import Path

import pytest

from il2ks.devtools.anonymize import FAKE_UUID_PREFIX, anonymize_text
from tests.conftest import FIXTURE_LOGS

SPAWN = (
    "T:15 AType:10 PLID:276479 PID:277503 BUL:2000 SH:0 BOMB:0 RCT:0 (1.0,2.0,3.0) "
    "IDS:6f3b5e69-38d7-4d83-868c-4e7b8129f41a LOGIN:60dc67e3-ffb2-4df3-a6e5-579e945b4018 NAME:=FB=Some Pilot "
    "TYPE:F-51D COUNTRY:601 FORM:0 FIELD:0 INAIR:0 PARENT:-1 ISPL:1 ISTSTART:1 PAYLOAD:0 FUEL:1.000 "
    "SKIN:my skin.dds WM:1\n"
)
CONNECT = "T:20 AType:20 USERID:60dc67e3-ffb2-4df3-a6e5-579e945b4018 USERNICKID:6f3b5e69-38d7-4d83-868c-4e7b8129f41a\n"
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def test_replaces_names_uuids_and_skins_consistently() -> None:
    out = anonymize_text(SPAWN + CONNECT + SPAWN)

    assert "Some Pilot" not in out
    assert "my skin" not in out
    assert out.count("NAME:Player001 TYPE:") == 2
    assert all(u.startswith(FAKE_UUID_PREFIX) for u in UUID_RE.findall(out))
    # The same real account maps to the same fake one across lines.
    login = re.search(r"LOGIN:(\S+)", out)
    assert login is not None
    assert f"USERID:{login.group(1)}" in out


def test_leaves_object_names_alone() -> None:
    line = "T:53 AType:12 ID:61440 TYPE:Landing Ship, Tank COUNTRY:601 NAME:Ship PID:-1 POS(1,2,3) MID:-1\n"
    assert anonymize_text(line) == line


def fixture_files() -> list[Path]:
    return sorted(FIXTURE_LOGS.glob("*.zip"))


@pytest.mark.parametrize("path", fixture_files(), ids=lambda p: p.name)
def test_committed_fixtures_are_anonymized(path: Path) -> None:
    with zipfile.ZipFile(path) as zf:
        text = zf.read(zf.namelist()[0]).decode("utf-8")
    assert all(u.startswith(FAKE_UUID_PREFIX) for u in UUID_RE.findall(text)), "real UUID in fixture"
    names = re.findall(r" AType:10 .* NAME:(.*?) TYPE:", text)
    assert all(re.fullmatch(r"Player\d{3}", n) for n in names), "real nickname in fixture"
    assert not re.search(r" SKIN:\S", text), "skin name in fixture"
