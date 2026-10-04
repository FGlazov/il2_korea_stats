"""`[rules]` toggles (TD-16): defaults, TOML values, validation."""

from pathlib import Path

import pytest

from il2ks.config import ConfigError, load_config
from il2ks.core.logparse.events import Pos
from il2ks.core.replay.toggles import RuleToggles
from tests.unit.replay.builder import FAR, NO, Scenario, by_acct


def _load(tmp_path: Path, text: str):  # noqa: ANN202
    file = tmp_path / "il2ks.toml"
    file.write_text(text, encoding="utf-8")
    return load_config(file, {"IL2KS_DATA_DIR": str(tmp_path / "data")})


def test_defaults_match_current_behaviour(tmp_path: Path) -> None:
    cfg = load_config(None, {"IL2KS_DATA_DIR": str(tmp_path / "data")})
    assert cfg.rules == RuleToggles()
    assert cfg.rules.credit_rams is True  # OQ-89: on by default
    assert (cfg.rules.ram_window_s, cfg.rules.ram_distance_m) == (0.5, 15.0)  # OQ-92: the tighter thresholds
    assert cfg.warnings == ()


def test_rules_section_is_read(tmp_path: Path) -> None:
    cfg = _load(tmp_path, "[rules]\ncredit_rams = true\nparachute_deaths = false\nram_window_s = 3\n")
    assert cfg.rules.credit_rams is True
    assert cfg.rules.ram_window_s == 3.0
    assert cfg.replay.toggles == cfg.rules  # the replay reads them from its rules: every entry point sees them


def test_the_removed_parachute_deaths_key_only_warns(tmp_path: Path) -> None:
    """OQ-99: a pilot killed while parachuting is always a death; old configs still load, with a warning."""
    cfg = _load(tmp_path, "[rules]\nparachute_deaths = false\n")
    assert not hasattr(cfg.rules, "parachute_deaths")
    assert len(cfg.warnings) == 1
    assert "rules.parachute_deaths" in cfg.warnings[0]


def test_invalid_rules_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"rules\.credit_rams"):
        _load(tmp_path, '[rules]\ncredit_rams = "maybe"\n')
    with pytest.raises(ConfigError, match=r"rules\.ram_distance_m"):
        _load(tmp_path, "[rules]\nram_distance_m = 0\n")


def test_the_config_reaches_the_replay(tmp_path: Path) -> None:
    """`cfg.replay` is what ingest, live and reprocess hand to the replay: the toggles change its result."""
    sc = Scenario()
    sc.fly_a()
    sc.fly_b()
    sc.kill(110, NO, 100)
    sc.kill(110.04, NO, 200, pos=Pos(FAR.x + 5.0, FAR.y, FAR.z))
    sc.end(110.2, 100, 101)
    sc.end(110.2, 200, 201)
    off = _load(tmp_path, "[rules]\ncredit_rams = false\n")
    on = _load(tmp_path, "")  # the default is on (OQ-89)
    assert by_acct(sc.result(off.replay), 1).kills_air == 0
    assert by_acct(sc.result(on.replay), 1).kills_air == 1
