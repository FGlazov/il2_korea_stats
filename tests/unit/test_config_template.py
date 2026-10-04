"""The shipped example `il2ks.toml` lists every setting with its default and can't drift from `Config` (FR-OPS-2)."""

import dataclasses
import tomllib
from importlib.resources import files
from pathlib import Path
from typing import cast

import pytest

from il2ks.config import (
    BackupConfig,
    Config,
    HttpsConfig,
    IngestConfig,
    LeaderboardConfig,
    LiveConfig,
    LogsConfig,
    WebConfig,
    load_config,
)
from il2ks.core.killboard import KillboardRules
from il2ks.core.ratings.elo import RatingRules
from il2ks.core.ratings.score import ScoreRules
from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.toggles import RuleToggles
from il2ks.core.stat_marks import MarkRules

TOP_LEVEL_KEYS = {"data_dir", "log_level", "log_keep_days", "debug"}
SERVER_KEYS = {"timezone", "uid"}  # `Config.timezone_name` / `Config.server_uid`, stored under [server]
TOURS_KEYS = {"mode", "start", "timezone"}  # `Config.tours` (a `TourRules`: the mode string parses into mode + days)


def template_lines() -> list[str]:
    return files("il2ks").joinpath("data", "il2ks.example.toml").read_text(encoding="utf-8").splitlines()


def is_setting_line(line: str) -> bool:
    """`#key = value` and `#[section]` (no space after the `#`) are settings; `# text` is an explanation."""
    return line.startswith("#") and len(line) > 1 and line[1] != " "


def uncommented() -> str:
    return "\n".join(line[1:] for line in template_lines() if is_setting_line(line))


def test_template_is_fully_commented_out() -> None:
    assert all(not line.strip() or line.startswith("#") for line in template_lines())


def test_template_lists_every_config_key() -> None:
    raw = tomllib.loads(uncommented())
    expected_sections = {
        "logs": {f.name for f in dataclasses.fields(LogsConfig)},
        "ingest": {f.name for f in dataclasses.fields(IngestConfig)},
        "live": {f.name for f in dataclasses.fields(LiveConfig)},
        "replay": {f.name for f in dataclasses.fields(ReplayRules)}
        - {"toggles"},  # the toggles are the [rules] section
        "rules": {f.name for f in dataclasses.fields(RuleToggles)},
        "ratings": {f.name for f in dataclasses.fields(RatingRules)},
        "marks": {f.name for f in dataclasses.fields(MarkRules)},
        "score": {f.name for f in dataclasses.fields(ScoreRules)}
        | {f.name for f in dataclasses.fields(LeaderboardConfig)},
        "killboard": {f.name for f in dataclasses.fields(KillboardRules)},
        "backup": {f.name for f in dataclasses.fields(BackupConfig)},
        "tours": TOURS_KEYS,
        "server": SERVER_KEYS,
        "web": {f.name for f in dataclasses.fields(WebConfig)},
        "https": {f.name for f in dataclasses.fields(HttpsConfig)},
    }
    top = {k for k, v in raw.items() if not isinstance(v, dict)}
    assert top == TOP_LEVEL_KEYS
    sections = {k: set(cast(dict[str, object], v)) for k, v in raw.items() if isinstance(v, dict)}
    assert sections == expected_sections


def test_config_fields_are_all_covered_by_the_template() -> None:
    """A new `Config` field must be a template key or an explicit exception here."""
    covered = {
        "data_dir",
        "log_level",
        "log_keep_days",
        "debug",
        "web",
        "https",
        "logs",
        "ingest",
        "live",
        "replay",
        "ratings",
        "marks",
        "score",
        "leaderboards",
        "board",
        "backup",
        "tours",
        "server_uid",
        "timezone_name",
    }
    not_settings = {"source"}  # the file that was read, not a setting
    assert {f.name for f in dataclasses.fields(Config)} == covered | not_settings


def test_template_values_equal_the_defaults(tmp_path: Path) -> None:
    """Uncommented, the template must load to exactly what no file at all gives."""
    env = {"IL2KS_DATA_DIR": str(tmp_path / "data")}
    file = tmp_path / "il2ks.toml"
    file.write_text(uncommented(), encoding="utf-8")
    from_template = load_config(file, env)
    defaults = load_config(None, env)
    assert dataclasses.replace(from_template, source=None) == defaults
    assert from_template.source == file


def test_template_numbers_are_written_as_the_defaults_are() -> None:
    """Spot check that equality above isn't vacuous: the template does set values (not just empty strings)."""
    raw = tomllib.loads(uncommented())
    assert raw["ingest"]["settle_seconds"] == 300
    assert raw["ingest"]["retry_backoff_minutes"] == [5, 30, 120]
    assert raw["replay"]["bailout_min_distance_m"] == ReplayRules().bailout_min_distance_m
    assert raw["log_keep_days"] == 14


@pytest.mark.parametrize("setting", [line for line in template_lines() if is_setting_line(line) and "=" in line])
def test_every_setting_is_explained_on_the_line_above(setting: str) -> None:
    lines = template_lines()
    above = lines[lines.index(setting) - 1]
    assert above.startswith("# "), f"{setting!r} has no explanation above it"
