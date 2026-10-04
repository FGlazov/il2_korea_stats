"""The Windows installer's build tooling (packaging/windows, doc 07 option B, NFR-INS-1..4, NFR-OFF-1).

The installer itself (il2ks.iss) can only be compiled and run on Windows by CI; these tests pin down what can be checked
anywhere: the pinned inputs, the WinSW service definition, the time zone table, and the installer's switches.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import cast
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import pytest
from build import BuildError, crlf, numeric_version, parse_pins, read_version, sha256_of
from quoting import quote_arg
from winsw import RESTART_DELAYS, STOP_TIMEOUT, ServiceSpec, render_service_xml
from zones import WINDOWS_TO_IANA, render_pascal

from il2ks import __version__

PACKAGING = Path(__file__).resolve().parents[2] / "packaging" / "windows"
REPO = PACKAGING.parents[1]
PINS_TEXT = (PACKAGING / "pins.toml").read_text(encoding="utf-8")
ISS_TEXT = (PACKAGING / "il2ks.iss").read_text(encoding="utf-8")
SUPERVISOR_GRACE_S = 15  # il2ks.serving.supervisor.Supervisor.grace_s


# --- pins.toml ------------------------------------------------------------------------------------


def test_pins_file_is_valid_and_complete() -> None:
    pins = parse_pins(PINS_TEXT)
    assert {"python", "caddy", "winsw", "inno_setup", "innounp"} <= set(pins)
    for pin in pins.values():
        assert pin.url.startswith("https://")
        assert re.fullmatch(r"[0-9a-f]{64}", pin.sha256)


def test_pinned_versions_appear_in_their_download_urls() -> None:
    """Bumping `version` without `url` (or the other way round) would ship something else than documented."""
    for name, pin in parse_pins(PINS_TEXT).items():
        assert pin.version in pin.url.replace("%2B", "+").replace("_", "."), name


def test_bundled_python_satisfies_requires_python() -> None:
    project = cast(dict[str, dict[str, object]], tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8")))
    requirement = cast(str, project["project"]["requires-python"])
    minimum = tuple(int(part) for part in requirement.removeprefix(">=").split("."))
    bundled = tuple(int(part) for part in parse_pins(PINS_TEXT)["python"].version.split("."))
    assert bundled[: len(minimum)] >= minimum
    assert "windows-msvc-install_only" in parse_pins(PINS_TEXT)["python"].url  # a relocatable, Windows x64 build


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('[a]\nversion = "1"\nurl = "https://x/y"\n', "sha256"),
        ('[a]\nversion = "1"\nurl = "http://x/y"\nsha256 = "' + "0" * 64 + '"\n', "https"),
        ('[a]\nversion = "1"\nurl = "https://x/y"\nsha256 = "ABC"\n', "64 lowercase hex"),
        ("a = 1\n", "must be a table"),
    ],
)
def test_parse_pins_rejects_incomplete_entries(text: str, message: str) -> None:
    with pytest.raises(BuildError, match=message):
        parse_pins(text)


def test_pin_filename_is_the_decoded_last_url_segment() -> None:
    pin = parse_pins(PINS_TEXT)["python"]
    assert pin.filename.startswith("cpython-3.13.")
    assert "+" in pin.filename
    assert "%" not in pin.filename


def test_sha256_of(tmp_path: Path) -> None:
    file = tmp_path / "x"
    file.write_bytes(b"abc")
    assert sha256_of(file) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


# --- versions and small helpers -------------------------------------------------------------------


def test_read_version_matches_the_package() -> None:
    assert read_version() == __version__


@pytest.mark.parametrize(
    ("version", "expected"),
    [("0.0.1", "0.0.1.0"), ("1.2.3", "1.2.3.0"), ("0.3.1rc2", "0.3.1.0"), ("2.0", "2.0.0.0"), ("1.2.3.4", "1.2.3.4")],
)
def test_numeric_version(version: str, expected: str) -> None:
    assert numeric_version(version) == expected


def test_crlf_is_idempotent() -> None:
    assert crlf("a\nb\r\nc") == "a\r\nb\r\nc"
    assert crlf(crlf("a\nb")) == "a\r\nb"


# --- the WinSW service ----------------------------------------------------------------------------


def _service() -> ET.Element:
    return ET.fromstring(render_service_xml())


def test_service_runs_il2ks_run_with_the_installed_config() -> None:
    root = _service()
    assert root.findtext("id") == "il2ks"
    assert root.findtext("executable") == "%BASE%\\..\\python\\python.exe"
    arguments = root.findtext("arguments") or ""
    assert arguments.startswith("-P -m il2ks ")  # -P: the data folder (the working directory) is not on sys.path
    assert '--config "%ProgramData%\\il2ks\\il2ks.toml"' in arguments
    assert arguments.endswith(" run")  # the global --config comes before the subcommand (argparse)
    assert root.findtext("workingdirectory") == "%ProgramData%\\il2ks"


def test_service_puts_the_bundled_caddy_on_the_path() -> None:
    """`il2ks run` finds Caddy via [https] caddy_path, then the PATH, then <data dir>/bin (caddy.find_caddy)."""
    env = {e.get("name"): e.get("value") for e in _service().findall("env")}
    assert env["PATH"] == "%BASE%\\..\\bin;%PATH%"
    assert env["PYTHONUNBUFFERED"] == "1"


def test_service_starts_at_boot_and_restarts_after_a_crash() -> None:
    root = _service()
    assert root.findtext("startmode") == "Automatic"
    actions = [(e.get("action"), e.get("delay")) for e in root.findall("onfailure")]
    assert actions == [("restart", delay) for delay in RESTART_DELAYS]
    assert root.findtext("resetfailure")


def test_service_gets_longer_to_stop_than_il2ks_needs() -> None:
    """WinSW sends Ctrl+C and, after `stoptimeout`, kills the process tree; il2ks run needs its grace period."""
    seconds = int(STOP_TIMEOUT.split()[0])
    assert (_service().findtext("stoptimeout") or "") == STOP_TIMEOUT
    assert seconds > SUPERVISOR_GRACE_S


def test_service_is_polite_to_the_game_server() -> None:
    assert _service().findtext("priority") == "belownormal"  # NFR-INS-5


def test_service_xml_is_escaped_and_parameterised() -> None:
    spec = ServiceSpec(service_id="il2ks-b", description='Stats & "more" <b>', config="D:\\x y\\il2ks.toml")
    root = ET.fromstring(render_service_xml(spec))
    assert root.findtext("id") == "il2ks-b"
    assert root.findtext("description") == 'Stats & "more" <b>'
    assert "D:\\x y\\il2ks.toml" in (root.findtext("arguments") or "")


# --- time zones -----------------------------------------------------------------------------------


def test_every_iana_name_in_the_zone_table_exists() -> None:
    for windows, iana in WINDOWS_TO_IANA:
        ZoneInfo(iana)  # raises for a typo; tzdata is a dependency of il2ks
        assert windows


def test_zone_table_has_no_duplicate_windows_names_and_knows_the_common_ones() -> None:
    names = [windows for windows, _ in WINDOWS_TO_IANA]
    assert len(names) == len(set(names))
    table = dict(WINDOWS_TO_IANA)
    assert table["W. Europe Standard Time"] == "Europe/Berlin"
    assert table["Korea Standard Time"] == "Asia/Seoul"
    assert table["UTC"] == "UTC"


def test_zones_render_as_a_pascal_function() -> None:
    code = render_pascal()
    assert code.startswith("function WindowsToIana(const WindowsName: String): String;")
    assert code.rstrip().endswith("end;")
    assert "WindowsName = 'Korea Standard Time' then Result := 'Asia/Seoul'" in code
    assert code.count(";") == 4  # header, Result := '', the whole if-chain (one statement), end;
    assert code.count("else if") == len(WINDOWS_TO_IANA) - 1
    for windows, iana in WINDOWS_TO_IANA:
        assert "'" not in windows + iana  # nothing that would need Pascal quote escaping


# --- il2ks.iss ------------------------------------------------------------------------------------


def test_installer_switches_used_in_code_are_documented_in_its_header() -> None:
    header = ISS_TEXT.split("[Setup]", 1)[0]
    used = set(re.findall(r"\bParam\('([A-Z]+)'\)", ISS_TEXT)) | set(re.findall(r"SwitchGiven\('([A-Z]+)'\)", ISS_TEXT))
    assert {"LOGDIR", "TIMEZONE", "DOMAIN", "EMAIL", "HTTPS", "ADMINUSER", "ADMINPASSWORDFILE", "NOSERVICE"} <= used
    for name in used:
        assert f"/{name}" in header, name


def test_installer_keeps_the_app_id_and_the_data_outside_program_files() -> None:
    assert "AppId={{6F1D2C3A-8B7E-4D5F-9A10-2E4B6C8D0F13}" in ISS_TEXT  # changing it breaks upgrades
    assert "DefaultDirName={autopf}\\il2ks" in ISS_TEXT
    assert "{commonappdata}\\il2ks" in ISS_TEXT
    assert "uninsneveruninstall" in ISS_TEXT  # data survives uninstall unless the user asks


def test_installer_detection_snippet_runs_against_the_real_detection_code(tmp_path: Path) -> None:
    """The wizard pre-fills the log folder with this one-liner (run with the installed Python): it must keep working."""
    match = re.search(r"DetectCode = '([^']+)';", ISS_TEXT)
    assert match is not None
    env = {"USERPROFILE": str(tmp_path), "HOME": str(tmp_path), "SystemRoot": str(tmp_path), "PATH": ""}
    done = subprocess.run(
        [sys.executable, "-c", match.group(1)], env=env, capture_output=True, text=True, timeout=60, check=False
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == ""  # nothing to find in an empty home folder


@pytest.mark.parametrize(
    ("value", "quoted"),
    [
        ("D:\\IL-2\\logs", '"D:\\IL-2\\logs"'),
        ("D:\\IL-2\\logs\\", '"D:\\IL-2\\logs\\\\"'),  # the trailing backslash is doubled: it must not escape the quote
        ("D:\\x\\\\", '"D:\\x\\\\\\\\"'),
        ("C:\\Program Files (x86)\\il2ks", '"C:\\Program Files (x86)\\il2ks"'),
        ('a"b', '"ab"'),  # a quote cannot be part of a Windows path
        ("", '""'),
    ],
)
def test_quote_arg(value: str, quoted: str) -> None:
    assert quote_arg(value) == quoted


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' own argument parser")
@pytest.mark.parametrize(
    "value",
    ["D:\\IL-2\\logs\\", "D:\\IL 2 (x86)\\logs\\\\", "C:\\", "D:\\a b\\c", "Europe/Berlin", "O'Brien & Sons <x>"],
)
def test_quote_arg_survives_the_real_windows_parser(value: str) -> None:
    """`il2ks setup --logs-dir D:\\logs\\ --timezone UTC` arrives as two arguments, not one swallowed command line."""
    code = "import sys, json; print(json.dumps(sys.argv[1:]))"
    command = f'"{sys.executable}" -c "{code}" {quote_arg(value)} {quote_arg("next")}'
    done = subprocess.run(command, capture_output=True, text=True, timeout=60, check=True)
    assert json.loads(done.stdout) == [value.replace('"', ""), "next"]


def test_installer_quotes_every_path_it_passes_and_never_puts_the_password_on_a_command_line() -> None:
    assert "function QuoteArg" in ISS_TEXT
    # no hand-quoted "--option "..." arguments left (those break on a trailing backslash)
    assert not re.search(r"--[a-z-]+ \"'", ISS_TEXT)
    assert not re.search(r"config \"' \+", ISS_TEXT)
    # the password goes through a locked-down file, not through the environment or the command line
    assert "IL2KS_ADMIN_PASSWORD'" not in ISS_TEXT
    assert "SetEnvironmentVariable" not in ISS_TEXT
    assert "--admin-password-file" in ISS_TEXT
    assert "ADMINPASSWORDFILE" in ISS_TEXT.split("[Setup]", 1)[0]


def test_installer_runs_python_in_safe_path_mode_and_the_service_as_its_virtual_account() -> None:
    """Python with -P never imports from the current folder; the service has no rights beyond its own data (OQ-41)."""
    for call in re.findall(r"RunCaptured\(PythonExe, ([^;]+)\)", ISS_TEXT):
        assert call.startswith("PythonSafeFlag") or call.startswith("Args"), call
    assert "Args := PythonSafeFlag + " in ISS_TEXT
    assert "PythonSafeFlag = '-P '" in ISS_TEXT
    assert "ServiceAccount = 'NT SERVICE\\il2ks'" in ISS_TEXT
    assert "obj= \"' + ServiceAccount" in ISS_TEXT  # sc config il2ks obj= "NT SERVICE\il2ks"
    assert "(OI)(CI)M" in ISS_TEXT  # Modify on the data folder, inherited


def test_batch_files_use_safe_python_and_quote_safely() -> None:
    run = (PACKAGING / "files" / "il2ks.cmd").read_text(encoding="utf-8")
    assert 'python.exe" -P -m il2ks %*' in run
    admin = (PACKAGING / "files" / "il2ks-admin.cmd").read_text(encoding="utf-8")
    assert "-ArgumentList '%*'" not in admin  # an apostrophe or quote in the arguments broke the PowerShell command
    assert "'%~f0'" not in admin  # so did one in the folder name
    assert "$env:IL2KS_ADMIN_ARGS" in admin
    code_lines = [line for line in admin.splitlines() if not line.lower().startswith("rem")]
    assert not any(line.rstrip().endswith("(") or line.strip() == ")" for line in code_lines)  # no (...) blocks


def test_windows_batch_files_have_no_unix_line_endings_after_the_build_step() -> None:
    for name in ("il2ks.cmd", "il2ks-admin.cmd"):
        text = (PACKAGING / "files" / name).read_text(encoding="utf-8")
        converted = crlf(text)
        assert "\n" not in converted.replace("\r\n", "")
