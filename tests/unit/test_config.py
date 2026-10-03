"""Configuration loading (TD-11, FR-OPS-2): defaults, il2ks.toml, IL2KS_* overrides, validation."""

import uuid
from datetime import timedelta
from pathlib import Path

import pytest

from il2ks.config import SERVER_UID_FILE, ConfigError, load_config
from il2ks.core.replay.config import ReplayRules


def write_toml(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_without_a_file(tmp_path: Path) -> None:
    cfg = load_config(None, {"IL2KS_DATA_DIR": str(tmp_path / "data")})
    assert cfg.data_dir == tmp_path / "data"
    assert cfg.source is None
    assert cfg.logs.dir is None
    assert cfg.logs.after_archive == "move"
    assert cfg.logs.remote is False
    assert cfg.ingest.watch_interval_s == 30
    assert cfg.ingest.retry_backoff == (timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2))
    assert cfg.replay == ReplayRules()
    assert cfg.log_level == "INFO"
    assert cfg.archive_dir == tmp_path / "data" / "archive"
    assert cfg.move_to == tmp_path / "data" / "ingested-logs"


def test_toml_values_and_relative_paths_resolve_against_the_file(tmp_path: Path) -> None:
    file = write_toml(
        tmp_path / "il2ks.toml",
        """
        data_dir = "data"
        log_level = "debug"

        [logs]
        dir = "dserver/logs"
        after_archive = "keep"
        remote = true

        [ingest]
        idle_minutes = 20
        stable_seconds = 5
        watch_interval_s = 10
        retry_backoff_minutes = [1, 2]

        [replay]
        bailout_min_distance_m = 250
        disconnect_window_s = 45

        [server]
        timezone = "Europe/Lisbon"
        uid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        """,
    )
    cfg = load_config(file, {})
    base = tmp_path.resolve()
    assert cfg.source == file
    assert cfg.data_dir == base / "data"
    assert cfg.logs.dir == base / "dserver" / "logs"
    assert cfg.logs.after_archive == "keep"
    assert cfg.logs.remote is True
    assert cfg.log_level == "DEBUG"
    assert cfg.ingest.idle_minutes == 20
    assert cfg.ingest.stable_seconds == 5
    assert cfg.ingest.retry_backoff == (timedelta(minutes=1), timedelta(minutes=2))
    assert cfg.replay.bailout_min_distance_m == 250
    assert cfg.replay.disconnect_window_s == 45
    assert cfg.replay.mission_end_window_s == ReplayRules().mission_end_window_s
    assert cfg.timezone_name == "Europe/Lisbon"
    assert cfg.server_uid == uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert not (cfg.data_dir / SERVER_UID_FILE).exists()


def test_env_overrides_the_file(tmp_path: Path) -> None:
    file = write_toml(
        tmp_path / "il2ks.toml",
        '[logs]\ndir = "from_file"\nafter_archive = "keep"\n[replay]\ndied_with_aircraft_s = 1\n',
    )
    env = {
        "IL2KS_DATA_DIR": str(tmp_path / "d"),
        "IL2KS_LOGS_DIR": str(tmp_path / "from_env"),
        "IL2KS_LOGS_AFTER_ARCHIVE": "delete",
        "IL2KS_LOGS_REMOTE": "yes",
        "IL2KS_INGEST_RETRY_BACKOFF_MINUTES": "1, 3",
        "IL2KS_REPLAY_DIED_WITH_AIRCRAFT_S": "2.5",
        "IL2KS_SERVER_TIMEZONE": "UTC",
        "IL2KS_LOG_LEVEL": "warning",
    }
    cfg = load_config(file, env)
    assert cfg.data_dir == tmp_path / "d"
    assert cfg.logs.dir == tmp_path / "from_env"
    assert cfg.logs.after_archive == "delete"
    assert cfg.logs.remote is True
    assert cfg.ingest.retry_backoff == (timedelta(minutes=1), timedelta(minutes=3))
    assert cfg.replay.died_with_aircraft_s == 2.5
    assert cfg.log_level == "WARNING"


def test_config_file_is_found_through_env_then_data_dir(tmp_path: Path) -> None:
    other = write_toml(tmp_path / "other.toml", '[logs]\ndir = "x"\n')
    assert load_config(None, {"IL2KS_CONFIG": str(other), "IL2KS_DATA_DIR": str(tmp_path / "d")}).source == other


def test_data_dir_file_is_used_when_nothing_else_names_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # no ./il2ks.toml here
    data = tmp_path / "d"
    data.mkdir()
    in_data = write_toml(data / "il2ks.toml", "")
    assert load_config(None, {"IL2KS_DATA_DIR": str(data)}).source == in_data


def test_missing_explicit_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml", {})
    with pytest.raises(ConfigError, match="IL2KS_CONFIG"):
        load_config(None, {"IL2KS_CONFIG": str(tmp_path / "nope.toml")})


@pytest.mark.parametrize(
    ("toml", "message"),
    [
        ("this is not toml", "il2ks.toml"),
        ('[logs]\nafter_archive = "shred"', r"logs\.after_archive"),
        ('log_level = "LOUD"', "log_level"),
        ("[ingest]\nidle_minutes = 0", "greater than 0"),
        ("[ingest]\nstable_seconds = -1", "negative"),
        ('[ingest]\nretry_backoff_minutes = [5, "x"]', "retry_backoff_minutes"),
        ("[ingest]\nretry_backoff_minutes = [5, 0]", "greater than 0"),
        ('[replay]\nbailout_min_distance_m = "far"', "bailout_min_distance_m"),
        ('[server]\ntimezone = "Mars/Olympus"', "timezone"),
        ('[server]\nuid = "not-a-uuid"', "server.uid"),
        ("logs = 3", "must be a table"),
        ('[logs]\nremote = "maybe"', "true or false"),
    ],
)
def test_invalid_values_name_the_setting(tmp_path: Path, toml: str, message: str) -> None:
    file = write_toml(tmp_path / "il2ks.toml", toml)
    with pytest.raises(ConfigError, match=message):
        load_config(file, {"IL2KS_DATA_DIR": str(tmp_path / "d")})


def test_server_uid_is_generated_once_and_then_kept(tmp_path: Path) -> None:
    env = {"IL2KS_DATA_DIR": str(tmp_path / "d")}
    first = load_config(None, env)
    stored = (tmp_path / "d" / SERVER_UID_FILE).read_text(encoding="utf-8").strip()
    assert stored == str(first.server_uid)
    assert load_config(None, env).server_uid == first.server_uid


def test_server_uid_is_not_written_when_asked_not_to(tmp_path: Path) -> None:
    load_config(None, {"IL2KS_DATA_DIR": str(tmp_path / "d")}, create_server_uid=False)
    assert not (tmp_path / "d").exists()


def test_timezone_defaults_to_the_tz_env_var_else_utc(tmp_path: Path) -> None:
    base = {"IL2KS_DATA_DIR": str(tmp_path / "d")}
    assert load_config(None, {**base, "TZ": "Europe/Lisbon"}).timezone_name == "Europe/Lisbon"
    assert load_config(None, {**base, "IL2KS_SERVER_TIMEZONE": "Asia/Seoul"}).timezone.key == "Asia/Seoul"
