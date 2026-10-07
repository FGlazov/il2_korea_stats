"""`il2ks setup`, `createadmin`, `backup`, `restore`, the automatic backup triggers (FR-OPS-1, FR-OPS-6, FR-ADM-1)."""

from __future__ import annotations

import argparse
import os
import tomllib
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core import management
from django.db.migrations.executor import MigrationExecutor

from il2ks.cli import main
from il2ks.config import Config, ConfigError, IngestConfig, LogsConfig, load_config
from il2ks.exitcodes import EXIT_FAILED, EXIT_LOCKED, EXIT_OK, EXIT_USAGE
from il2ks.ingest import watch as watch_mod
from il2ks.ingest.lock import LockBusyError, WriterLock
from il2ks.ops import backup, migrate
from il2ks.ops.detect import LogFolder
from il2ks.ops.setup import SetupOptions, run_setup
from il2ks.ops.template import template_text
from tests.ingest_fakes import FakeSteps, make_pipeline
from tests.ops_helpers import ScriptedPrompter, make_instance, read_notes, recording, returning

PASSWORD = "Tr1cky-Horse-Battery-9"
T = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A working folder with a clean IL2KS_* environment; commands run in it and may only touch tmp_path."""
    for name in list(os.environ):
        if name.startswith("IL2KS_") and name != "IL2KS_TEST_DB":
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IL2KS_DATA_DIR", str(tmp_path / "data"))  # restored afterwards, even if a command changes it
    return tmp_path


SETUP_ARGS = ["--non-interactive", "--timezone", "Asia/Seoul", "--admin-username", "boss"]


def setup_args(root: Path, *extra: str) -> list[str]:
    return ["setup", *SETUP_ARGS, "--data-dir", str(root / "data"), "--logs-dir", str(root / "logs"), *extra]


# --- setup ----------------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_non_interactive_setup_end_to_end(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (sandbox / "logs").mkdir()
    monkeypatch.setenv("IL2KS_ADMIN_PASSWORD", PASSWORD)
    args = setup_args(
        sandbox, "--domain", "https://Stats.Example.com/", "--https", "caddy", "--email", "me@example.com"
    )

    assert main(args) == EXIT_OK

    config_file = sandbox / "il2ks.toml"
    text = config_file.read_text(encoding="utf-8")
    raw = tomllib.loads(text)
    assert raw["data_dir"] == str(sandbox / "data")
    assert raw["logs"] == {"dir": str(sandbox / "logs")}
    assert raw["server"]["timezone"] == "Asia/Seoul"
    assert raw["server"]["uid"] == (sandbox / "data" / "server_uid.txt").read_text(encoding="utf-8").strip()
    assert raw["https"] == {"mode": "caddy", "domain": "stats.example.com", "email": "me@example.com"}
    # The comments of the shipped example are all still there.
    explanations = [line for line in template_text().splitlines() if line.startswith("# ")]
    assert all(line in text.splitlines() for line in explanations)
    cfg = load_config(config_file, {})
    assert cfg.timezone_name == "Asia/Seoul"
    assert cfg.logs.dir == sandbox / "logs"
    assert (sandbox / "data").is_dir()
    user = get_user_model().objects.get(username="boss")
    assert user.is_staff
    assert user.is_superuser
    assert user.check_password(PASSWORD)


@pytest.mark.django_db
def test_setup_never_overwrites_an_existing_config_without_force(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("IL2KS_ADMIN_PASSWORD", PASSWORD)
    assert main(setup_args(sandbox)) == EXIT_OK
    config_file = sandbox / "il2ks.toml"
    first = config_file.read_text(encoding="utf-8")
    capsys.readouterr()

    assert main(setup_args(sandbox, "--domain", "other.example.com")) == EXIT_USAGE

    assert config_file.read_text(encoding="utf-8") == first
    assert "already set up" in capsys.readouterr().out
    assert not list(sandbox.glob("il2ks.toml.bak-*"))


@pytest.mark.django_db
def test_setup_force_keeps_a_copy_and_the_server_id(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IL2KS_ADMIN_PASSWORD", PASSWORD)
    assert main(setup_args(sandbox)) == EXIT_OK
    config_file = sandbox / "il2ks.toml"
    first = config_file.read_text(encoding="utf-8")
    uid = tomllib.loads(first)["server"]["uid"]

    assert main(setup_args(sandbox, "--force", "--domain", "other.example.com")) == EXIT_OK

    copies = list(sandbox.glob("il2ks.toml.bak-*"))
    assert len(copies) == 1
    assert copies[0].read_text(encoding="utf-8") == first
    raw = tomllib.loads(config_file.read_text(encoding="utf-8"))
    assert raw["https"]["domain"] == "other.example.com"
    assert raw["server"]["uid"] == uid  # a restarted setup is still the same server (TD-17)


@pytest.mark.django_db
def test_setup_takes_every_answer_from_the_environment_too(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IL2KS_LOGS_DIR", str(sandbox / "logs"))
    monkeypatch.setenv("IL2KS_SERVER_TIMEZONE", "Europe/Berlin")
    monkeypatch.setenv("IL2KS_HTTPS_MODE", "external")
    monkeypatch.setenv("IL2KS_HTTPS_DOMAIN", "stats.example.org")
    monkeypatch.setenv("IL2KS_ADMIN_USERNAME", "envadmin")
    monkeypatch.setenv("IL2KS_ADMIN_PASSWORD", PASSWORD)
    assert main(["setup", "--non-interactive"]) == EXIT_OK
    raw = tomllib.loads((sandbox / "il2ks.toml").read_text(encoding="utf-8"))
    assert raw["server"]["timezone"] == "Europe/Berlin"
    assert raw["https"] == {"mode": "external", "domain": "stats.example.org"}
    assert get_user_model().objects.filter(username="envadmin").exists()


@pytest.mark.django_db
def test_setup_without_a_password_still_finishes_and_says_how_to_add_the_admin(
    sandbox: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(setup_args(sandbox)) == EXIT_OK
    assert "il2ks createadmin" in capsys.readouterr().out
    assert not get_user_model().objects.filter(username="boss").exists()


def test_setup_rejects_an_unknown_timezone_and_writes_nothing(
    sandbox: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["setup", "--non-interactive", "--timezone", "Mars/Base"]) == EXIT_USAGE
    assert "unknown timezone" in capsys.readouterr().out
    assert not (sandbox / "il2ks.toml").exists()


def test_setup_rejects_a_bad_domain(sandbox: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["setup", "--non-interactive", "--domain", "not a domain"]) == EXIT_USAGE
    assert "not a domain name" in capsys.readouterr().out


@pytest.mark.django_db
def test_setup_with_a_weak_admin_password_reports_it_and_exits_one(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("IL2KS_ADMIN_PASSWORD", "12345678")
    assert main(setup_args(sandbox)) == EXIT_FAILED
    out = capsys.readouterr().out
    assert "NOT created" in out
    assert (sandbox / "il2ks.toml").is_file()  # the rest of the setup stands
    assert not get_user_model().objects.filter(username="boss").exists()


def fake_finder(*folders: Path) -> Callable[[], list[LogFolder]]:
    return lambda: [LogFolder(p, 12, 1_790_000_000.0) for p in folders]


@pytest.mark.django_db
def test_interactive_setup_offers_the_detected_folders_and_defaults(sandbox: Path) -> None:
    logs = sandbox / "found" / "logs"
    logs.mkdir(parents=True)
    io = ScriptedPrompter(
        # data folder, log folder (candidate 1), timezone, domain, https mode (default 1), e-mail
        answers=[None, "1", "Asia/Seoul", "https://Stats.Example.com/", None, "me@example.com", None],
        secrets=[PASSWORD, PASSWORD],
    )
    env = {"IL2KS_DATA_DIR": str(sandbox / "data")}
    opts = SetupOptions(config_path=sandbox / "il2ks.toml")

    code = run_setup(opts, io, env=env, platform="linux", find_logs=fake_finder(logs))

    assert code == EXIT_OK
    assert f"Data folder [{sandbox / 'data'}]" in " ".join(io.asked)
    assert str(logs) in io.transcript
    raw = tomllib.loads((sandbox / "il2ks.toml").read_text(encoding="utf-8"))
    assert raw["logs"]["dir"] == str(logs.resolve())
    assert raw["https"] == {"mode": "caddy", "domain": "stats.example.com", "email": "me@example.com"}
    assert get_user_model().objects.get(username="admin").check_password(PASSWORD)
    for hint in ("il2ks doctor", "il2ks run", "il2ks service"):
        assert hint in io.transcript


@pytest.mark.django_db
def test_interactive_setup_asks_again_for_a_bad_timezone_and_can_skip_the_log_folder(sandbox: Path) -> None:
    io = ScriptedPrompter(
        answers=[None, "", "Mars/Base", "UTC", "", "2"],  # no log folder, bad zone then UTC, no domain, own proxy
        confirms=[False],  # no admin account now
    )
    env = {"IL2KS_DATA_DIR": str(sandbox / "data")}
    code = run_setup(
        SetupOptions(config_path=sandbox / "il2ks.toml"), io, env=env, platform="win32", find_logs=fake_finder()
    )
    assert code == EXIT_OK
    assert "not a known time zone" in io.transcript
    assert "Windows cannot tell il2ks" in io.transcript
    raw = tomllib.loads((sandbox / "il2ks.toml").read_text(encoding="utf-8"))
    assert "logs" not in raw
    assert raw["server"]["timezone"] == "UTC"
    assert raw["https"] == {"mode": "external"}
    assert "Set [logs] dir" in io.transcript  # the next steps tell the admin what is still open


@pytest.mark.django_db
def test_rerunning_the_database_and_admin_steps_is_safe(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IL2KS_ADMIN_PASSWORD", PASSWORD)
    assert main(setup_args(sandbox)) == EXIT_OK
    assert main(setup_args(sandbox, "--force")) == EXIT_OK
    assert get_user_model().objects.filter(username="boss").count() == 1


# --- createadmin ----------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_createadmin_creates_then_resets(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("IL2KS_ADMIN_PASSWORD", PASSWORD)
    assert main(["createadmin", "--username", "chief"]) == EXIT_OK
    assert "created" in capsys.readouterr().out
    user = get_user_model().objects.get(username="chief")
    assert user.is_superuser
    assert user.is_staff

    user.is_active = False
    user.save()
    monkeypatch.setenv("IL2KS_ADMIN_PASSWORD", "Another-Pass-Word-77")
    assert main(["createadmin", "--username", "chief"]) == EXIT_OK
    assert "reset" in capsys.readouterr().out
    user.refresh_from_db()
    assert user.is_active
    assert user.check_password("Another-Pass-Word-77")


@pytest.mark.django_db
def test_createadmin_if_none_creates_the_first_admin_but_never_touches_an_existing_one(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The Docker image runs this on every start with the admin from env vars (docs/install-docker.md)."""
    monkeypatch.setenv("IL2KS_ADMIN_PASSWORD", PASSWORD)
    assert main(["createadmin", "--if-none", "--username", "first"]) == EXIT_OK
    assert "created" in capsys.readouterr().out
    monkeypatch.setenv("IL2KS_ADMIN_PASSWORD", "Another-Pass-Word-77")
    assert main(["createadmin", "--if-none", "--username", "second"]) == EXIT_OK
    assert "already exists" in capsys.readouterr().out
    assert not get_user_model().objects.filter(username="second").exists()
    assert get_user_model().objects.get(username="first").check_password(PASSWORD)  # not reset to the new env value


@pytest.mark.django_db
def test_createadmin_reads_the_password_from_a_file(sandbox: Path) -> None:
    password_file = sandbox / "pw.txt"
    password_file.write_text(PASSWORD + "\r\n", encoding="utf-8")
    assert main(["createadmin", "--username", "filed", "--password-file", str(password_file)]) == EXIT_OK
    assert get_user_model().objects.get(username="filed").check_password(PASSWORD)


@pytest.mark.django_db
def test_createadmin_applies_the_password_validators(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for weak in ("short", "12345678901", "password1234", "chief123"):
        monkeypatch.setenv("IL2KS_ADMIN_PASSWORD", weak)
        assert main(["createadmin", "--username", "chief"]) == EXIT_USAGE, weak
    assert not get_user_model().objects.filter(username="chief").exists()


def test_createadmin_without_a_terminal_needs_username_and_password(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["createadmin"]) == EXIT_USAGE
    assert "--username is required" in capsys.readouterr().err
    assert main(["createadmin", "--username", "x"]) == EXIT_USAGE
    assert "no password" in capsys.readouterr().err
    assert main(["createadmin", "--username", "x", "--password-file", str(sandbox / "missing")]) == EXIT_USAGE


@pytest.mark.django_db
def test_createadmin_interactive_asks_twice_and_retries(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from il2ks.ops import commands

    monkeypatch.setattr(commands, "interactive_terminal", returning(True))
    io = ScriptedPrompter(answers=["pilot"], secrets=["Aaaa-bbbb-1111", "Different-1111", PASSWORD, PASSWORD])
    ns = _namespace()
    assert commands.cmd_createadmin(ns, env={}, io=io) == EXIT_OK
    assert "The two passwords differ" in io.transcript
    assert get_user_model().objects.get(username="pilot").check_password(PASSWORD)


def _namespace() -> argparse.Namespace:
    return argparse.Namespace(config=None, username=None, password_file=None, email=None, wait=5.0, if_none=False)


# --- backup and restore commands ------------------------------------------------------------------------------------


@pytest.fixture
def instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    for name in list(os.environ):
        if name.startswith("IL2KS_") and name != "IL2KS_TEST_DB":
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(backup, "web_probably_running", returning(False))
    return make_instance(tmp_path)


def test_backup_command_writes_a_zip_and_says_what_it_leaves_out(
    instance: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    assert instance.source is not None
    assert main(["--config", str(instance.source), "backup"]) == EXIT_OK
    out = capsys.readouterr().out
    assert len(backup.list_backups(instance.backup_dir)) == 1
    assert "NOT" in out
    assert "mission logs" in out


def test_backup_command_copies_to_the_second_folder_and_mirrors_the_archive(
    tmp_path: Path, instance: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    second = tmp_path / "second"
    cfg = make_instance(
        tmp_path / "with-copy", extra_toml=f'[backup]\ncopy_to = "{second.as_posix()}"\ncopy_archive = true\n'
    )
    assert cfg.source is not None
    (cfg.archive_dir / "2026" / "10").mkdir(parents=True)
    (cfg.archive_dir / "2026" / "10" / "m.txt.zip").write_bytes(b"log")
    assert main(["--config", str(cfg.source), "backup"]) == EXIT_OK
    out = capsys.readouterr().out
    assert len(backup.list_backups(second / str(cfg.server_uid))) == 1
    assert (second / str(cfg.server_uid) / "archive" / "2026" / "10" / "m.txt.zip").read_bytes() == b"log"
    assert "Copy:" in out


def test_backup_command_succeeds_even_when_the_second_folder_cannot_be_written(
    tmp_path: Path, instance: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("a file, not a folder", encoding="utf-8")
    cfg = make_instance(tmp_path / "with-copy", extra_toml=f'[backup]\ncopy_to = "{blocked.as_posix()}"\n')
    assert cfg.source is not None
    assert main(["--config", str(cfg.source), "backup"]) == EXIT_OK
    assert len(backup.list_backups(cfg.backup_dir)) == 1
    assert "COPY FAILED" in capsys.readouterr().out


def test_backup_command_reports_a_crashed_copy_not_a_stale_ok(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 0.2.0 L5: a copy thread that died with an exception used to show the previous run's OK."""
    second = tmp_path / "second"
    cfg = make_instance(tmp_path / "with-copy", extra_toml=f'[backup]\ncopy_to = "{second.as_posix()}"\n')
    assert cfg.source is not None
    assert main(["--config", str(cfg.source), "backup"]) == EXIT_OK
    assert "Copy:" in capsys.readouterr().out  # an OK status is now on disk

    def boom(config: Config) -> None:
        raise RuntimeError("share exploded")

    monkeypatch.setattr(backup, "copy_to_second_folder", boom)
    assert main(["--config", str(cfg.source), "backup"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "COPY FAILED" in out
    assert "share exploded" in out
    assert "Copy: " not in out.replace("COPY FAILED", "")


def test_backup_command_without_a_database_is_an_error(
    tmp_path: Path, instance: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = make_instance(tmp_path / "empty", with_db=False)
    assert empty.source is not None
    assert main(["--config", str(empty.source), "backup"]) == EXIT_USAGE
    assert "il2ks setup" in capsys.readouterr().err


@pytest.mark.sqlite_only
def test_restore_command_round_trip_and_safety_backup(instance: Config, capsys: pytest.CaptureFixture[str]) -> None:
    assert instance.source is not None
    zip_path = backup.create_backup(instance, now=lambda: T)
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(instance.db_path)) as conn:
        conn.execute("INSERT INTO notes (text) VALUES ('later')")
        conn.commit()

    assert main(["--config", str(instance.source), "restore", str(zip_path), "--yes"]) == EXIT_OK

    assert read_notes(instance.db_path) == ["first"]
    reasons = sorted(str(p.name) for p in backup.list_backups(instance.backup_dir))
    assert len(reasons) == 2  # the one restored from, plus the safety backup of the state before
    assert "Restored and verified" in capsys.readouterr().out


@pytest.mark.sqlite_only
def test_restore_command_refuses_while_a_writer_runs(instance: Config, capsys: pytest.CaptureFixture[str]) -> None:
    assert instance.source is not None
    zip_path = backup.create_backup(instance, now=lambda: T)
    with WriterLock(instance.data_dir, "watch"):
        assert main(["--config", str(instance.source), "restore", str(zip_path), "--yes"]) == EXIT_LOCKED
    assert "another il2ks writer is running" in capsys.readouterr().err


@pytest.mark.sqlite_only
def test_restore_command_needs_a_confirmation(instance: Config, capsys: pytest.CaptureFixture[str]) -> None:
    assert instance.source is not None
    zip_path = backup.create_backup(instance, now=lambda: T)
    assert main(["--config", str(instance.source), "restore", str(zip_path)]) == EXIT_USAGE
    assert "--yes" in capsys.readouterr().err


@pytest.mark.sqlite_only
def test_restore_command_declined_in_a_terminal_changes_nothing(
    instance: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    from il2ks.ops import commands

    assert instance.source is not None
    zip_path = backup.create_backup(instance, now=lambda: T)
    monkeypatch.setattr(commands, "interactive_terminal", returning(True))

    ns = argparse.Namespace(config=instance.source, zip=zip_path, yes=False, force=False)
    io = ScriptedPrompter(answers=[], confirms=[False])
    assert commands.cmd_restore(ns, env={}, io=io) == EXIT_OK
    assert "Nothing changed" in io.transcript
    assert len(backup.list_backups(instance.backup_dir)) == 1  # no safety backup was made


@pytest.mark.sqlite_only
def test_restore_command_refuses_while_the_website_seems_to_run_unless_forced(
    instance: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from il2ks.ops import commands

    assert instance.source is not None
    zip_path = backup.create_backup(instance, now=lambda: T)
    monkeypatch.setattr(backup, "web_probably_running", returning(True))
    io = ScriptedPrompter(answers=[])
    ns = argparse.Namespace(config=instance.source, zip=zip_path, yes=True, force=False)
    assert commands.cmd_restore(ns, env={}, io=io) == EXIT_LOCKED
    assert "website is probably running" in capsys.readouterr().err
    assert len(backup.list_backups(instance.backup_dir)) == 1  # nothing was touched: no safety backup either

    forced = argparse.Namespace(config=instance.source, zip=zip_path, yes=True, force=True)
    assert commands.cmd_restore(forced, env={}, io=io) == EXIT_OK
    assert "website is probably running" in io.transcript  # still said, then done anyway


@pytest.mark.sqlite_only
def test_restore_command_refuses_while_il2ks_run_holds_the_run_lock(
    instance: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The web server no longer holds the writer lock, so the run lock is what shows that the stack is up."""
    from il2ks.ops import commands
    from il2ks.serving import procutil

    assert instance.source is not None
    zip_path = backup.create_backup(instance, now=lambda: T)
    monkeypatch.setattr(backup, "web_probably_running", returning(False))
    ns = argparse.Namespace(config=instance.source, zip=zip_path, yes=True, force=False)
    with procutil.run_lock(instance.data_dir):
        assert commands.cmd_restore(ns, env={}, io=ScriptedPrompter(answers=[])) == EXIT_LOCKED
    assert "il2ks run is running" in capsys.readouterr().err


@pytest.mark.sqlite_only
def test_restore_writes_the_config_where_the_next_start_reads_it(
    instance: Config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without a loaded config file the target is `--config`, then IL2KS_CONFIG, then the current folder."""
    from il2ks.ops import commands

    assert instance.source is not None
    assert commands.restore_config_target(instance, None, {}) == instance.source.resolve()
    bare = replace(instance, source=None)
    explicit = tmp_path / "explicit.toml"
    assert commands.restore_config_target(bare, explicit, {"IL2KS_CONFIG": "ignored.toml"}) == explicit.resolve()
    from_env = tmp_path / "from-env.toml"
    assert commands.restore_config_target(bare, None, {"IL2KS_CONFIG": str(from_env)}) == from_env.resolve()
    monkeypatch.chdir(tmp_path)
    assert commands.restore_config_target(bare, None, {}) == (tmp_path / "il2ks.toml").resolve()


@pytest.mark.parametrize("what", ["missing", "garbage"])
@pytest.mark.sqlite_only
def test_restore_command_rejects_a_bad_zip(instance: Config, what: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert instance.source is not None
    bad = instance.data_dir.parent / "bad.zip"
    if what == "garbage":
        bad.write_bytes(b"nope")
    assert main(["--config", str(instance.source), "restore", str(bad), "--yes"]) == EXIT_USAGE
    assert "cannot read" in capsys.readouterr().err
    assert read_notes(instance.db_path) == ["first"]


# --- automatic triggers ---------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_pending_migrations_trigger_a_backup_before_they_are_applied(
    instance: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    monkeypatch.setattr(MigrationExecutor, "migration_plan", returning([("fake", False)]))
    monkeypatch.setattr(management, "call_command", recording(order, "migrate"))
    monkeypatch.setattr(backup, "backup_before_migration", recording(order, "backup"))
    migrate.migrate_if_needed(instance, "ingest", wait=None)
    assert order == ["backup", "migrate"]


@pytest.mark.django_db
def test_no_pending_migrations_means_no_backup(instance: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    monkeypatch.setattr(backup, "backup_before_migration", recording(order, "backup"))
    assert migrate.migrate_if_needed(instance, "ingest", wait=None) is None
    assert order == []


@pytest.mark.django_db
def test_a_failed_pre_migration_backup_stops_the_migration(instance: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    applied: list[str] = []

    def failing(cfg: Config) -> None:
        raise backup.BackupError("disk full")

    monkeypatch.setattr(MigrationExecutor, "migration_plan", returning([("fake", False)]))
    monkeypatch.setattr(management, "call_command", recording(applied, "migrate"))
    monkeypatch.setattr(backup, "backup_before_migration", failing)
    with pytest.raises(backup.BackupError):
        migrate.migrate_if_needed(instance, "ingest", wait=None)
    assert applied == []


def watch_cfg(instance: Config, tmp_path: Path) -> Config:
    (tmp_path / "logs").mkdir(exist_ok=True)
    return replace(
        instance,
        logs=LogsConfig(dir=tmp_path / "logs"),
        ingest=IngestConfig(watch_interval_s=0.01),
    )


@pytest.mark.django_db
def test_the_watch_loop_makes_the_daily_backup_once(instance: Config, tmp_path: Path) -> None:
    cfg = watch_cfg(instance, tmp_path)
    clock = T
    watch_mod.watch(cfg, make_pipeline(FakeSteps()), max_ticks=3, now=lambda: clock)
    found = backup.list_backups(cfg.backup_dir)
    assert len(found) == 1
    assert backup.backup_time(found[0]) is not None


@pytest.mark.django_db
def test_a_failing_daily_backup_does_not_stop_the_watch_loop_and_is_retried_later(
    instance: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = watch_cfg(instance, tmp_path)
    calls: list[datetime] = []
    clock = [T]

    def now() -> datetime:
        clock[0] += timedelta(seconds=1)
        return clock[0]

    def failing(config: Config, at: datetime) -> None:
        calls.append(at)
        raise OSError("disk full")

    monkeypatch.setattr(watch_mod, "backup_if_due", failing)
    assert watch_mod.watch(cfg, make_pipeline(FakeSteps()), max_ticks=3, now=now) == 3
    assert len(calls) == 1  # the failure held the next attempt off for an hour


def test_the_daily_backup_retry_gate(instance: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[datetime] = []

    def failing(config: Config, at: datetime) -> None:
        calls.append(at)
        raise OSError("disk full")

    monkeypatch.setattr(watch_mod, "backup_if_due", failing)
    retry = watch_mod.daily_backup(instance, T, None)
    assert retry == T + timedelta(hours=1)
    assert watch_mod.daily_backup(instance, T + timedelta(minutes=30), retry) == retry
    assert len(calls) == 1
    assert watch_mod.daily_backup(instance, T + timedelta(minutes=61), retry) == T + timedelta(minutes=121)
    assert len(calls) == 2
    monkeypatch.setattr(watch_mod, "backup_if_due", returning(None))
    assert watch_mod.daily_backup(instance, T + timedelta(hours=3), retry) is None


# --- config ---------------------------------------------------------------------------------------------------------


def test_backup_settings_come_from_file_and_environment(tmp_path: Path) -> None:
    file = tmp_path / "il2ks.toml"
    file.write_text("[backup]\nkeep = 3\ndaily = false\n", encoding="utf-8")
    cfg = load_config(file, {"IL2KS_DATA_DIR": str(tmp_path)})
    assert (cfg.backup.keep, cfg.backup.daily) == (3, False)
    cfg = load_config(file, {"IL2KS_DATA_DIR": str(tmp_path), "IL2KS_BACKUP_KEEP": "7", "IL2KS_BACKUP_DAILY": "yes"})
    assert (cfg.backup.keep, cfg.backup.daily) == (7, True)


@pytest.mark.parametrize("keep", ["0", "-1", "2.5", "many"])
def test_backup_keep_must_be_a_whole_number_above_zero(tmp_path: Path, keep: str) -> None:
    with pytest.raises(ConfigError, match=r"backup\.keep"):
        load_config(None, {"IL2KS_DATA_DIR": str(tmp_path), "IL2KS_BACKUP_KEEP": keep})


@pytest.mark.parametrize("command", ["setup", "createadmin", "doctor", "backup", "restore"])
def test_every_ops_command_has_help(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main([command, "--help"])
    assert info.value.code == 0
    assert "usage: il2ks" in capsys.readouterr().out


def test_doctor_help_documents_the_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["doctor", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "0 = all OK, 1 = warnings only, 2 = at least one error" in out


@pytest.mark.django_db
def test_checking_for_pending_migrations_does_not_need_the_writer_lock(instance: Config) -> None:
    """Regression: `il2ks web` waited 60 s and exited 3 while a long ingest held the lock, only to look (FR-ING-20)."""
    with WriterLock(instance.data_dir, "ingest"):
        assert migrate.migrate_if_needed(instance, "web", wait=None) is None


@pytest.mark.django_db
def test_pending_migrations_need_the_writer_lock(instance: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    applied: list[str] = []
    monkeypatch.setattr(MigrationExecutor, "migration_plan", returning([("fake", False)]))
    monkeypatch.setattr(management, "call_command", recording(applied, "migrate"))
    monkeypatch.setattr(backup, "backup_before_migration", returning(None))
    with WriterLock(instance.data_dir, "ingest"), pytest.raises(LockBusyError):
        migrate.migrate_if_needed(instance, "web", wait=None)
    assert applied == []


@pytest.mark.django_db
def test_the_plan_is_checked_again_under_the_lock(instance: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    """Another process migrated while we waited for the lock: nothing to apply, no backup."""
    plans: list[list[tuple[str, bool]]] = [[("fake", False)], []]
    applied: list[str] = []

    def next_plan(*args: object, **kwargs: object) -> list[tuple[str, bool]]:
        return plans.pop(0)

    monkeypatch.setattr(MigrationExecutor, "migration_plan", next_plan)
    monkeypatch.setattr(management, "call_command", recording(applied, "migrate"))
    assert migrate.migrate_if_needed(instance, "web", wait=None) is None
    assert applied == []


# --- admin unlock ---------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_admin_unlock_removes_the_locks(sandbox: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from axes.models import AccessAttempt  # pyright: ignore[reportMissingTypeStubs]

    for name, address in (("boss", "203.0.113.5"), ("boss", "198.51.100.9"), ("deputy", "203.0.113.5")):
        AccessAttempt.objects.create(username=name, ip_address=address, failures_since_start=5)

    assert main(["admin", "unlock", "--ip", "198.51.100.9"]) == EXIT_OK
    assert "1 " in capsys.readouterr().out
    assert AccessAttempt.objects.count() == 2
    assert main(["admin", "unlock", "--user", "deputy"]) == EXIT_OK
    assert sorted(AccessAttempt.objects.values_list("username", flat=True)) == ["boss"]
    assert main(["admin", "unlock", "--all"]) == EXIT_OK
    assert AccessAttempt.objects.count() == 0
    assert main(["admin", "unlock", "--all"]) == EXIT_OK
    assert "nothing" in capsys.readouterr().out.lower()


def test_admin_unlock_needs_to_be_told_what_to_unlock(sandbox: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["admin", "unlock"]) == EXIT_USAGE
    assert "--all" in capsys.readouterr().err
