"""Autostart on the manual path (doc 07 option C): systemd unit and Windows task text. Nothing here touches the machine:
the `--install` paths run against a recording fake (a test that really installed a service would be a bug)."""

import argparse
import dataclasses
import os
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path

import pytest

from il2ks.cli import EXIT_FAILED, EXIT_OK, EXIT_USAGE, main
from il2ks.config import Config, HttpsConfig, load_config
from il2ks.serving import commands, service


def make(tmp_path: Path, *, config_file: bool = True, mode: str = "caddy", https_port: int = 443) -> Config:
    env = {"IL2KS_DATA_DIR": str(tmp_path / "data")}
    base = load_config(None, env, create_server_uid=False)
    source = tmp_path / "il2ks.toml" if config_file else None
    https = dataclasses.replace(
        HttpsConfig(), https_port=https_port, mode="external" if mode == "external" else "caddy"
    )
    return dataclasses.replace(base, source=source, https=https)


PY = "/opt/il2ks/bin/python"


def test_systemd_unit_runs_il2ks_run_with_restart_and_low_port_capability(tmp_path: Path) -> None:
    unit = service.render_systemd_unit(make(tmp_path), name="il2ks", user="stats", python=PY)
    lines = unit.splitlines()
    assert "[Unit]" in lines
    assert "[Service]" in lines
    assert "[Install]" in lines
    exec_start = next(line for line in lines if line.startswith("ExecStart="))
    assert exec_start.startswith(f"ExecStart={PY} -m il2ks --config ")
    assert exec_start.endswith(" run")
    assert "User=stats" in lines
    assert "Restart=on-failure" in lines
    assert "KillSignal=SIGTERM" in lines
    assert "AmbientCapabilities=CAP_NET_BIND_SERVICE" in lines
    assert "WantedBy=multi-user.target" in lines
    assert "After=network-online.target" in lines


def test_external_mode_or_high_ports_need_no_low_port_capability(tmp_path: Path) -> None:
    assert "AmbientCapabilities" not in service.render_systemd_unit(make(tmp_path, mode="external"), python=PY)
    cfg = dataclasses.replace(make(tmp_path), https=HttpsConfig(http_port=8080, https_port=8443))
    assert "AmbientCapabilities" not in service.render_systemd_unit(cfg, python=PY)


def test_without_a_config_file_the_data_dir_travels_as_environment(tmp_path: Path) -> None:
    unit = service.render_systemd_unit(make(tmp_path, config_file=False), python=PY)
    assert any(line.startswith("Environment=") and "IL2KS_DATA_DIR=" in line for line in unit.splitlines())
    assert "--config" not in unit


def test_paths_with_spaces_are_quoted_for_systemd(tmp_path: Path) -> None:
    spaced = tmp_path / "my stats"
    cfg = dataclasses.replace(make(tmp_path), source=spaced / "il2ks.toml")
    exec_line = next(
        line
        for line in service.render_systemd_unit(cfg, python="/opt/my env/bin/python").splitlines()
        if line.startswith("ExecStart=")
    )
    assert exec_line.startswith('ExecStart="/opt/my env/bin/python" -m il2ks --config "')


def test_percent_signs_are_escaped_for_systemd() -> None:
    assert service._systemd_quote("100%") == "100%%"  # pyright: ignore[reportPrivateUsage]


def test_schtasks_command_runs_at_startup_as_the_user_and_asks_for_the_password(tmp_path: Path) -> None:
    args = service.schtasks_create_args(make(tmp_path), name="il2ks", user="PC\\admin", python="C:\\il2ks\\python.exe")
    assert args[:4] == ["schtasks", "/Create", "/TN", "il2ks"]
    assert args[args.index("/SC") + 1] == "ONSTART"
    assert args[args.index("/RU") + 1] == "PC\\admin"
    assert args[args.index("/RP") + 1] == "*"
    task_run = args[args.index("/TR") + 1]
    assert task_run.startswith("C:\\il2ks\\python.exe -m il2ks --config ")
    assert task_run.endswith(" run")
    assert args[-1] == "/F"


def test_schtasks_system_account_needs_no_password(tmp_path: Path) -> None:
    args = service.schtasks_create_args(make(tmp_path), user="SYSTEM", python="python.exe")
    assert "/RP" not in args


def test_task_xml_is_valid_and_restarts_on_failure(tmp_path: Path) -> None:
    xml = service.render_task_xml(make(tmp_path), python="C:\\il2ks\\python.exe")
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    root = ET.fromstring(xml.split("?>", 1)[1])
    assert root.find("t:Triggers/t:BootTrigger", ns) is not None
    assert root.findtext("t:Settings/t:RestartOnFailure/t:Count", namespaces=ns) == "999"
    assert root.findtext("t:Settings/t:ExecutionTimeLimit", namespaces=ns) == "PT0S"
    assert root.findtext("t:Actions/t:Exec/t:Command", namespaces=ns) == "C:\\il2ks\\python.exe"
    arguments = root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=ns)
    assert arguments is not None
    assert arguments.startswith("-m il2ks --config ")
    assert arguments.endswith(" run")


def test_task_xml_is_written_as_utf16_with_bom(tmp_path: Path) -> None:
    path = tmp_path / "t.xml"
    service.write_task_xml(path, "<a>é</a>")
    raw = path.read_bytes()
    assert raw[:2] == b"\xff\xfe"
    assert raw[2:].decode("utf-16-le") == "<a>é</a>"


# --- the CLI: printing and writing never run anything; --install runs only through the injected runner ---------------


class Recorder:
    def __init__(self, code: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.code = code

    def __call__(self, args: Sequence[str]) -> int:
        self.calls.append(list(args))
        return self.code


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("IL2KS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("IL2KS_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def run_service(argv: list[str], runner: Recorder, monkeypatch: pytest.MonkeyPatch) -> int:
    """`il2ks service ...` through the real parser, with the system-touching runner replaced."""
    from il2ks import cli

    ns = cli._build_parser().parse_args(argv)  # pyright: ignore[reportPrivateUsage]
    hooks = commands.Hooks(django_setup=lambda: None, migrate=lambda cfg, name, wait: None, runner=runner)
    return commands.dispatch(ns, hooks)


def test_service_systemd_prints_the_unit_to_stdout(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["service", "systemd", "--user", "stats"]) == EXIT_OK
    out = capsys.readouterr()
    assert out.out.startswith("[Unit]")
    assert "User=stats" in out.out
    assert "--install" in out.err  # how to install is explained, on stderr so `> file` stays clean


def test_service_systemd_write_saves_the_unit_and_runs_nothing(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = env / "il2ks.service"
    assert main(["service", "systemd", "--write", str(target)]) == EXIT_OK
    assert target.read_text(encoding="utf-8").startswith("[Unit]")


def test_service_systemd_install_writes_and_enables_via_the_runner(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(service, "SYSTEMD_DIR", env / "systemd")
    (env / "systemd").mkdir()
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    runner = Recorder()
    assert run_service(["service", "systemd", "--install", "--name", "stats2"], runner, monkeypatch) == EXIT_OK
    assert (env / "systemd" / "stats2.service").is_file()
    assert runner.calls == [["systemctl", "daemon-reload"], ["systemctl", "enable", "--now", "stats2.service"]]


def test_service_systemd_install_needs_root(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    if os.name == "nt":
        pytest.skip("the root check is POSIX only")
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    runner = Recorder()
    assert run_service(["service", "systemd", "--install"], runner, monkeypatch) == EXIT_USAGE
    assert runner.calls == []


def test_service_systemd_install_reports_a_failing_command(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "SYSTEMD_DIR", env)
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    assert run_service(["service", "systemd", "--install"], Recorder(code=1), monkeypatch) == EXIT_FAILED


def test_service_schtasks_prints_the_command_and_runs_nothing(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["service", "schtasks", "--user", "PC\\admin"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "schtasks /Create /TN il2ks" in out
    assert "/SC ONSTART" in out


def test_service_schtasks_xml_writes_the_task_file(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    xml = env / "task.xml"
    assert main(["service", "schtasks", "--user", "SYSTEM", "--xml", str(xml)]) == EXIT_OK
    assert xml.read_bytes()[:2] == b"\xff\xfe"
    assert "/XML" in capsys.readouterr().out


def test_service_schtasks_install_goes_through_the_runner(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = Recorder()
    assert run_service(["service", "schtasks", "--install", "--user", "SYSTEM"], runner, monkeypatch) == EXIT_OK
    assert len(runner.calls) == 1
    assert runner.calls[0][:4] == ["schtasks", "/Create", "/TN", "il2ks"]
    assert "/XML" in runner.calls[0]
    assert (env / "data" / service.TASK_XML_NAME).is_file()


def test_the_arguments_exist_in_the_parser() -> None:
    from il2ks import cli

    parser = cli._build_parser()  # pyright: ignore[reportPrivateUsage]
    ns = parser.parse_args(["service", "systemd"])
    assert isinstance(ns, argparse.Namespace)
    assert ns.install is False
    assert ns.name == "il2ks"
