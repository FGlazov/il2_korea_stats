"""The ops commands in real, separate processes on a real SQLite file: the path an admin takes (FR-OPS-1, FR-OPS-6).

Slower than the in-process tests (each command starts Python and Django), so it is one test that walks the whole story:
setup, doctor, backup, a database that needs an update (automatic backup first), restore.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import zipfile
from pathlib import Path

from il2ks.ops import backup

PASSWORD = "Tr1cky-Horse-Battery-9"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def il2ks(cwd: Path, *args: str, check: bool = True, **env_vars: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("IL2KS_")}
    env["IL2KS_ADMIN_PASSWORD"] = PASSWORD
    env["PYTHONIOENCODING"] = "utf-8"
    env["IL2KS_WEB_PORT"] = str(_free_port())  # doctor reports a busy web port; another process may own 8000
    env.update(env_vars)
    result = subprocess.run(
        [sys.executable, "-m", "il2ks.cli", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    if check:
        assert result.returncode == 0, f"il2ks {' '.join(args)}\n{result.stdout}\n{result.stderr}"
    return result


def test_the_admin_story_in_real_processes(tmp_path: Path) -> None:
    data, logs, empty = tmp_path / "data", tmp_path / "logs", tmp_path / "empty"
    logs.mkdir()
    empty.mkdir()

    # 1. setup, without asking anything
    out = il2ks(
        tmp_path, "setup", "--non-interactive", "--data-dir", str(data), "--logs-dir", str(logs),
        "--timezone", "UTC", "--admin-username", "boss", "--https", "external",
    ).stdout  # fmt: skip
    assert "Database ready" in out
    assert "Admin account 'boss': created" in out
    assert (tmp_path / "il2ks.toml").is_file()
    assert (data / "il2ks.sqlite3").is_file()
    assert not list((data / "backups").glob("*")) if (data / "backups").exists() else True  # nothing to protect yet

    # 2. doctor: the database and config are fine; no backup yet and no mission reports are warnings, not errors
    doctor = il2ks(tmp_path, "doctor", "--json", check=False)
    report = json.loads(doctor.stdout)
    titles = {f["title"]: f["level"] for f in report["findings"]}
    assert titles["Configuration file found and valid"] == "OK"
    assert titles["Database is readable and up to date"] == "OK"
    assert titles["No backup yet"] == "WARN"
    assert doctor.returncode == report["exit_code"] == 1

    # 3. backup
    assert "Backup written" in il2ks(tmp_path, "backup").stdout
    [first] = backup.list_backups(data / "backups")
    with zipfile.ZipFile(first) as zf:
        assert {"manifest.json", "il2ks.sqlite3", "il2ks.toml", "server_uid.txt"} <= set(zf.namelist())

    # 4. the database is behind (as after an upgrade): the next writer command backs it up, then updates it
    il2ks(
        tmp_path, "manage", "migrate", "il2ks_db", "0001", "--no-input", IL2KS_DATA_DIR=str(data)
    )  # manage ignores il2ks.toml
    pending = json.loads(il2ks(tmp_path, "doctor", "--json", check=False).stdout)
    assert any("pending migration" in f["title"] for f in pending["findings"])
    il2ks(tmp_path, "ingest", "--from", str(empty))
    reasons: list[str] = []
    for path in backup.list_backups(data / "backups"):
        with zipfile.ZipFile(path) as zf:
            reasons.append(json.loads(zf.read("manifest.json"))["reason"])
    assert sorted(reasons) == ["manual", "pre-migrate"]
    fixed = json.loads(il2ks(tmp_path, "doctor", "--json", check=False).stdout)
    assert not any("pending migration" in f["title"] for f in fixed["findings"])

    # 5. restore the first backup over the current state
    restored = il2ks(tmp_path, "restore", str(first), "--yes")
    assert "Restored and verified" in restored.stdout
    assert len(backup.list_backups(data / "backups")) == 3  # plus the safety backup taken before the restore

    # 6. a second setup refuses and changes nothing
    again = il2ks(tmp_path, "setup", "--non-interactive", check=False)
    assert again.returncode == 2
    assert "already set up" in again.stdout
