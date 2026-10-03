"""Start `il2ks run` automatically at boot, on the manual install path (doc 07 option C): `il2ks service ...`.

Everything here only *renders* text by default (a systemd unit, a `schtasks` command, a Windows task XML). Nothing
on the machine changes unless the admin passes `--install`, which is the only code path that runs `systemctl` or
`schtasks`. Tests never use it. A real Windows service is the installer's job (iteration 1.x), not this file's.
"""

import getpass
import os
import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from xml.sax.saxutils import escape

from il2ks.config import Config

DEFAULT_NAME = "il2ks"
SYSTEMD_DIR = Path("/etc/systemd/system")
TASK_XML_NAME = "il2ks-task.xml"

type CommandRunner = Callable[[Sequence[str]], int]
"""Runs a system command and returns its exit code (the real one is `subprocess.run`)."""


def run_command(cfg: Config, python: str | None = None) -> list[str]:
    """The command line that starts the whole site: this Python (the one il2ks is installed in) and `il2ks run`."""
    argv = [python or sys.executable, "-m", "il2ks"]
    if cfg.source is not None:
        argv += ["--config", str(cfg.source.resolve())]
    return [*argv, "run"]


def _config_environment(cfg: Config) -> list[str]:
    """`Environment=` lines for installs that have no config file (everything comes from IL2KS_* variables)."""
    return [] if cfg.source is not None else [f"IL2KS_DATA_DIR={cfg.data_dir.resolve()}"]


def _systemd_quote(word: str) -> str:
    word = word.replace("%", "%%")
    if word and not any(c in word for c in " \t\"'\\"):
        return word
    return '"' + word.replace("\\", "\\\\").replace('"', '\\"') + '"'


def needs_low_ports(cfg: Config) -> bool:
    return cfg.https.mode == "caddy" and min(cfg.https.http_port, cfg.https.https_port) < 1024


def render_systemd_unit(
    cfg: Config, *, name: str = DEFAULT_NAME, user: str | None = None, python: str | None = None
) -> str:
    """A systemd unit for `il2ks run`: restarts on failure, stops with SIGTERM, may bind 80/443 without being root."""
    exec_start = " ".join(_systemd_quote(w) for w in run_command(cfg, python))
    lines = [
        "[Unit]",
        f"Description=il2ks stats site ({name})",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
    ]
    if user:
        lines.append(f"User={user}")
    lines.append(f"WorkingDirectory={_systemd_quote(str(cfg.data_dir.resolve()))}")
    lines += [f"Environment={_systemd_quote(env)}" for env in _config_environment(cfg)]
    lines += [
        "Environment=PYTHONUNBUFFERED=1",
        f"ExecStart={exec_start}",
        "Restart=on-failure",
        "RestartSec=5",
        "KillSignal=SIGTERM",
        "TimeoutStopSec=30",
    ]
    if needs_low_ports(cfg):
        lines.append("AmbientCapabilities=CAP_NET_BIND_SERVICE")  # Caddy listens on 80 and 443 without root
    lines += ["", "[Install]", "WantedBy=multi-user.target", ""]
    return "\n".join(lines)


def systemd_install_commands(name: str) -> list[list[str]]:
    return [["systemctl", "daemon-reload"], ["systemctl", "enable", "--now", f"{name}.service"]]


def default_windows_user() -> str:
    domain, user = os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME") or getpass.getuser()
    return f"{domain}\\{user}" if domain else user


def schtasks_create_args(cfg: Config, *, name: str = DEFAULT_NAME, user: str, python: str | None = None) -> list[str]:
    """`schtasks /Create ...`: run at startup (30 s after boot) as `user`; asks for the password."""
    task_run = subprocess.list2cmdline(run_command(cfg, python))
    args = ["schtasks", "/Create", "/TN", name, "/SC", "ONSTART", "/DELAY", "0000:30", "/RU", user]
    if user.upper() not in {"SYSTEM", "NT AUTHORITY\\SYSTEM"}:
        args += ["/RP", "*"]  # prompts for the password: tasks that run before anyone logs in need it stored
    return [*args, "/TR", task_run, "/F"]


def schtasks_xml_args(*, name: str, user: str, xml_path: Path) -> list[str]:
    args = ["schtasks", "/Create", "/TN", name, "/XML", str(xml_path), "/RU", user]
    if user.upper() not in {"SYSTEM", "NT AUTHORITY\\SYSTEM"}:
        args += ["/RP", "*"]
    return [*args, "/F"]


def render_task_xml(cfg: Config, *, python: str | None = None) -> str:
    """Windows Task Scheduler XML: at boot, restart up to 999 times (a minute apart) if `il2ks run` itself dies, no time
    limit, runs on battery. (`schtasks` flags alone can't express the restart policy.)"""
    argv = run_command(cfg, python)
    arguments = subprocess.list2cmdline(argv[1:])
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>il2ks stats site: web + log watcher + HTTPS proxy</Description>
  </RegistrationInfo>
  <Triggers>
    <BootTrigger>
      <Enabled>true</Enabled>
      <Delay>PT30S</Delay>
    </BootTrigger>
  </Triggers>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <AllowHardTerminate>true</AllowHardTerminate>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>999</Count>
    </RestartOnFailure>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(argv[0])}</Command>
      <Arguments>{escape(arguments)}</Arguments>
      <WorkingDirectory>{escape(str(cfg.data_dir.resolve()))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def write_task_xml(path: Path, xml: str) -> None:
    """Task Scheduler wants UTF-16 with a byte order mark."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe" + xml.encode("utf-16-le"))


def display(args: Sequence[str]) -> str:
    """A command as the admin would type it."""
    return subprocess.list2cmdline(list(args)) if os.name == "nt" else shlex.join(args)


def real_runner(args: Sequence[str]) -> int:
    return subprocess.run(list(args), check=False).returncode
