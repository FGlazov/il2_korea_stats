"""`[rules]` toggles (TD-16): defaults, TOML values, validation."""

from pathlib import Path

import pytest

from il2ks.config import ConfigError, load_config
from il2ks.core.replay.toggles import RuleToggles


def _load(tmp_path: Path, text: str):  # noqa: ANN202
    file = tmp_path / "il2ks.toml"
    file.write_text(text, encoding="utf-8")
    return load_config(file, {"IL2KS_DATA_DIR": str(tmp_path / "data")})


def test_defaults_match_current_behaviour(tmp_path: Path) -> None:
    cfg = load_config(None, {"IL2KS_DATA_DIR": str(tmp_path / "data")})
    assert cfg.rules == RuleToggles()
    assert cfg.rules.credit_rams is False
    assert cfg.rules.parachute_deaths is True


def test_rules_section_is_read(tmp_path: Path) -> None:
    cfg = _load(tmp_path, "[rules]\ncredit_rams = true\nparachute_deaths = false\nram_window_s = 3\n")
    assert cfg.rules.credit_rams is True
    assert cfg.rules.parachute_deaths is False
    assert cfg.rules.ram_window_s == 3.0


def test_invalid_rules_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"rules\.credit_rams"):
        _load(tmp_path, '[rules]\ncredit_rams = "maybe"\n')
    with pytest.raises(ConfigError, match=r"rules\.ram_distance_m"):
        _load(tmp_path, "[rules]\nram_distance_m = 0\n")
