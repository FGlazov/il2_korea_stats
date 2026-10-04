"""The WinSW service definition for the Windows installer (doc 07 option B): `il2ks run` as one Windows service.

WinSW (https://github.com/winsw/winsw) is a small .exe that registers itself as a Windows service and runs a program.
This module only renders its XML file, so a test can check it. The XML is rendered once at build time and uses
variables WinSW (and Windows) expand when the service starts, so one file fits every machine and install folder:

- `%BASE%` is the folder of the WinSW exe: `<install dir>\\service`.
- `%ProgramData%` is the data folder's parent: the data folder is `%ProgramData%\\il2ks`, and holds `il2ks.toml`.

How the service stops (checked, see packaging/windows/README.md): WinSW sends Ctrl+C to `il2ks run`, which stops web,
watch and Caddy in order (`il2ks.serving.procutil.stop_on_signals`) and exits within seconds; if it has not exited after
`stoptimeout` WinSW kills the whole process tree. Nothing has to be configured for that, and the timeout only has to be
longer than the supervisor's own grace period (15 s per child, `Supervisor.grace_s`).
"""

from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree as ET

SERVICE_ID = "il2ks"
SERVICE_NAME = "il2ks stats site"
DESCRIPTION = (
    "Statistics website for IL-2 Sturmovik: Korea: web server, log watcher and HTTPS proxy (il2ks run). "
    "Data and logs: %ProgramData%\\il2ks."
)
DATA_DIR = "%ProgramData%\\il2ks"
CONFIG_FILE = DATA_DIR + "\\il2ks.toml"
SERVICE_LOG_DIR = DATA_DIR + "\\logs\\service"

# Slightly below normal priority: the game server runs on the same machine (NFR-INS-5). Children inherit it.
PRIORITY = "belownormal"
STOP_TIMEOUT = "30 sec"  # > Supervisor.grace_s (15 s): il2ks gets the time to stop its children by itself
RESTART_DELAYS = ("5 sec", "10 sec", "30 sec", "60 sec")  # after a crash; the last delay repeats
RESET_FAILURE_AFTER = "1 hour"


@dataclass(frozen=True, slots=True)
class ServiceSpec:
    """What differs between services; the defaults are the installer's."""

    service_id: str = SERVICE_ID
    display_name: str = SERVICE_NAME
    description: str = DESCRIPTION
    python: str = "%BASE%\\..\\python\\python.exe"
    config: str = CONFIG_FILE
    working_dir: str = DATA_DIR
    bin_dir: str = "%BASE%\\..\\bin"  # Caddy lives here: put it on the PATH, where `il2ks run` looks for it (TD-23)
    log_dir: str = SERVICE_LOG_DIR


def render_service_xml(spec: ServiceSpec | None = None) -> str:
    """The WinSW XML configuration (`il2ks-service.xml`, next to `il2ks-service.exe`)."""
    spec = spec or ServiceSpec()
    root = ET.Element("service")

    def add(tag: str, text: str | None = None, **attrs: str) -> ET.Element:
        element = ET.SubElement(root, tag, attrs)
        if text is not None:
            element.text = text
        return element

    add("id", spec.service_id)
    add("name", spec.display_name)
    add("description", spec.description)
    add("executable", spec.python)
    add("arguments", f'-P -m il2ks --config "{spec.config}" run')  # -P: no current folder on sys.path
    add("workingdirectory", spec.working_dir)
    add("env", name="PATH", value=f"{spec.bin_dir};%PATH%")
    add("env", name="PYTHONUNBUFFERED", value="1")
    add("env", name="PYTHONUTF8", value="1")
    add("priority", PRIORITY)
    add("startmode", "Automatic")
    add("delayedAutoStart", "true")  # after boot has settled: the game server comes first
    for delay in RESTART_DELAYS:
        add("onfailure", action="restart", delay=delay)
    add("resetfailure", RESET_FAILURE_AFTER)
    add("stoptimeout", STOP_TIMEOUT)
    add("logpath", spec.log_dir)
    log = add("log", mode="roll-by-size")
    ET.SubElement(log, "sizeThreshold").text = "5120"  # KiB; il2ks writes its own daily logs, this only catches crashes
    ET.SubElement(log, "keepFiles").text = "4"
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
