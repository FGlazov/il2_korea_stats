"""`[tours]` config (TD-26): mode, start date, and a timezone that defaults to the server's."""

from datetime import date
from pathlib import Path

import pytest

from il2ks.config import ConfigError, load_config
from il2ks.core.tours import TourRules


def load(tmp_path: Path, toml: str = "", env: dict[str, str] | None = None) -> TourRules:
    file = tmp_path / "il2ks.toml"
    file.write_text(toml, encoding="utf-8")
    return load_config(file, {"IL2KS_DATA_DIR": str(tmp_path / "d"), **(env or {})}).tours


def test_defaults_are_monthly_in_the_server_timezone(tmp_path: Path) -> None:
    assert load(tmp_path) == TourRules(mode="monthly", timezone_name="UTC")
    assert load(tmp_path, '[server]\ntimezone = "Asia/Seoul"').timezone_name == "Asia/Seoul"


def test_tours_timezone_overrides_the_server_timezone(tmp_path: Path) -> None:
    rules = load(tmp_path, '[server]\ntimezone = "Asia/Seoul"\n[tours]\ntimezone = "Europe/Berlin"')
    assert rules.timezone_name == "Europe/Berlin"


def test_days_mode_reads_length_and_start(tmp_path: Path) -> None:
    rules = load(tmp_path, '[tours]\nmode = "days:14"\nstart = "2026-10-01"')
    assert (rules.mode, rules.days, rules.start) == ("days", 14, date(2026, 10, 1))
    assert rules.label == "days:14"


def test_an_unquoted_toml_date_works_too(tmp_path: Path) -> None:
    assert load(tmp_path, '[tours]\nmode = "days:7"\nstart = 2026-10-01').start == date(2026, 10, 1)


def test_manual_mode_ignores_a_start_date(tmp_path: Path) -> None:
    rules = load(tmp_path, '[tours]\nmode = "manual"\nstart = "2026-10-01"')
    assert (rules.mode, rules.start) == ("manual", None)


def test_environment_overrides(tmp_path: Path) -> None:
    rules = load(tmp_path, env={"IL2KS_TOURS_MODE": "days:3", "IL2KS_TOURS_START": "2026-01-05"})
    assert (rules.days, rules.start) == (3, date(2026, 1, 5))


@pytest.mark.parametrize(
    ("toml", "message"),
    [
        ('[tours]\nmode = "weekly"', "tours.mode"),
        ('[tours]\nmode = "days:0"', "tours.mode"),
        ('[tours]\nmode = "days:14"', "tours.start is required"),
        ('[tours]\nmode = "days:14"\nstart = "yesterday"', "tours.start must be a date"),
        ("[tours]\nmode = 5", "tours.mode"),
        ('[tours]\ntimezone = "Mars/Olympus"', "tours.timezone"),
    ],
)
def test_invalid_values_name_the_setting(tmp_path: Path, toml: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load(tmp_path, toml)
