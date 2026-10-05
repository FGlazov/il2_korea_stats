"""The admin's overrides of the game rules (maintainer decision 2026-10-05, FR-ADM-7): which settings, how they are
validated (by the same parser as `il2ks.toml`), and how they overlay the file's values."""

import re
from dataclasses import replace
from datetime import date
from importlib import resources
from pathlib import Path

import pytest

from il2ks.config import ConfigError, RuleSet, load_config
from il2ks.core.ratings.score import ScoreRules
from il2ks.core.tours import TourRules
from il2ks.rule_settings import BY_KEY, FIELDS, base_value, effective_rules, fields_of, overlay, sanitize, validate

MACHINE_SECTIONS = {"", "logs", "ingest", "live", "backup", "server", "web", "https"}  # il2ks.toml only
RULE_SECTIONS = {"replay", "rules", "ratings", "score", "marks", "killboard", "tours"}


def example_keys() -> list[tuple[str, str]]:
    """(section, key) of every setting `il2ks.example.toml` lists (they are commented out)."""
    text = (resources.files("il2ks") / "data" / "il2ks.example.toml").read_text(encoding="utf-8")
    section = ""
    found: list[tuple[str, str]] = []
    for line in text.splitlines():
        header = re.match(r"#?\[(\w+)\]", line)
        if header:
            section = header.group(1)
            continue
        setting = re.match(r"#(\w+) *=", line)
        if setting:
            found.append((section, setting.group(1)))
    return found


def test_every_setting_of_the_example_file_is_classified() -> None:
    """A new `il2ks.toml` setting must be put on one side: a game rule the admin can override (a `RuleField`) or a
    machine setting that stays in the file. Update docs/settings.md (the classification table) too."""
    for section, key in example_keys():
        if section in MACHINE_SECTIONS:
            assert (section, key) not in {(f.section, f.name) for f in FIELDS}, f"{section}.{key} is machine"
        else:
            assert section in RULE_SECTIONS, f"[{section}] is neither a game-rule nor a machine section"
            assert f"{section}.{key}" in BY_KEY, f"{section}.{key} is a game rule: add it to rule_settings"
    assert len(example_keys()) > 60


def test_every_field_is_in_the_example_file() -> None:
    listed = {f"{s}.{k}" for s, k in example_keys()}
    assert {f.key for f in FIELDS} <= listed


def test_a_field_reads_back_what_it_wrote() -> None:
    """`base_value` gives the shape the parser reads: overlaying every field's own value changes nothing."""
    base = RuleSet(
        score=replace(ScoreRules(), air_kill_pvp=7.0), tours=TourRules(mode="days", days=14, start=date(2026, 9, 1))
    )
    assert overlay(base, {f.key: base_value(base, f) for f in FIELDS}) == base


def test_an_admin_value_overrides_the_file() -> None:
    base = RuleSet(score=replace(ScoreRules(), air_kill_pvp=7.0))
    assert overlay(base, {}).score.air_kill_pvp == 7.0
    assert overlay(base, {"score.air_kill_pvp": 12.5}).score.air_kill_pvp == 12.5
    assert overlay(base, {"rules.credit_rams": False}).replay.toggles.credit_rams is False
    assert overlay(base, {"killboard.assists": True}).board.assists is True
    assert overlay(base, {"score.min_elo_games": 9}).marks.min_elo_games == 9  # the marks follow the boards
    assert overlay(base, {"tours.mode": "manual"}).tours.mode == "manual"
    assert overlay(base, {"ratings.k": 16}).ratings.k == 16.0


def test_a_blank_field_uses_the_file() -> None:
    base = RuleSet(score=replace(ScoreRules(), air_kill_pvp=7.0))
    clean, problems = validate(base, {"score.air_kill_pvp": "  ", "score.air_kill_ai": "3"})
    assert problems == []
    assert clean == {"score.air_kill_ai": 3.0}
    assert overlay(base, clean).score.air_kill_pvp == 7.0


def test_a_comma_decimal_is_accepted() -> None:
    clean, problems = validate(RuleSet(), {"score.air_kill_pvp": "2,5", "score.min_sorties": "4"})
    assert problems == []
    assert clean == {"score.air_kill_pvp": 2.5, "score.min_sorties": 4}


@pytest.mark.parametrize(
    ("key", "text", "toml"),
    [
        ("score.air_kill_pvp", "-1", "[score]\nair_kill_pvp = -1\n"),
        ("score.air_kill_pvp", "lots", '[score]\nair_kill_pvp = "lots"\n'),
        ("score.min_sorties", "2.5", "[score]\nmin_sorties = 2.5\n"),
        ("rules.ram_window_s", "0", "[rules]\nram_window_s = 0\n"),
        ("rules.credit_rams", "maybe", '[rules]\ncredit_rams = "maybe"\n'),
        ("marks.min_sorties", "0", "[marks]\nmin_sorties = 0\n"),
        ("ratings.k", "-3", "[ratings]\nk = -3\n"),
        ("tours.mode", "weekly", '[tours]\nmode = "weekly"\n'),
        ("tours.timezone", "Mars/Olympus", '[tours]\ntimezone = "Mars/Olympus"\n'),
        ("replay.bailout_min_distance_m", "-5", "[replay]\nbailout_min_distance_m = -5\n"),
    ],
)
def test_invalid_input_is_rejected_with_the_message_of_the_config_parser(
    tmp_path: Path, key: str, text: str, toml: str
) -> None:
    file = tmp_path / "il2ks.toml"
    file.write_text(toml, encoding="utf-8")
    with pytest.raises(ConfigError) as from_file:
        load_config(file, {"IL2KS_DATA_DIR": str(tmp_path / "data")})
    clean, problems = validate(RuleSet(), {key: text})
    assert clean == {} or problems
    assert problems == [str(from_file.value).removeprefix(f"{file}: ")]


def test_a_day_tour_needs_its_start_date() -> None:
    clean, problems = validate(RuleSet(), {"tours.mode": "days:14"})
    assert len(problems) == 1
    assert "tours.start" in problems[0]
    clean, problems = validate(RuleSet(), {"tours.mode": "days:14", "tours.start": "2026-09-01"})
    assert problems == []
    assert overlay(RuleSet(), clean).tours == TourRules(
        mode="days", days=14, start=date(2026, 9, 1), timezone_name="UTC"
    )
    _, problems = validate(RuleSet(), {"tours.mode": "monthly", "tours.start": "01.09.2026"})
    assert problems


def test_every_wrong_field_is_reported() -> None:
    _, problems = validate(RuleSet(), {"score.air_kill_pvp": "-1", "ratings.k": "-2", "score.air_kill_ai": "3"})
    assert len(problems) == 2


def test_stored_junk_is_dropped_and_a_bad_combination_never_raises() -> None:
    assert sanitize("nonsense") == {}
    assert sanitize({"score.air_kill_pvp": "ten", "nope.key": 1, "score.air_kill_ai": 4}) == {"score.air_kill_ai": 4}
    base = RuleSet()
    rules = effective_rules(base, {"tours.mode": "days:7", "score.air_kill_ai": 4})  # days without a start
    assert rules.score.air_kill_ai == 4.0
    assert rules.tours.mode == "monthly"


def test_the_effect_of_each_field() -> None:
    effects = {f.key: f.effect for f in FIELDS}
    assert effects["score.air_kill_pvp"] == "rescore"
    assert effects["killboard.assists"] == "rescore"
    assert effects["ratings.k"] == "rescore"
    assert effects["tours.mode"] == "retour"
    assert effects["rules.credit_rams"] == "reprocess"
    assert effects["replay.bailout_min_distance_m"] == "reprocess"
    assert effects["score.min_sorties"] == "display"
    assert effects["marks.min_sorties"] == "display"
    assert {f.page for f in FIELDS} == {"scoring", "tours", "rules", "leaderboards"}
    assert all(fields_of(page) for page in ("scoring", "tours", "rules", "leaderboards"))


def test_the_elo_minimum_changes_stored_ratings_so_it_needs_a_rebuild() -> None:
    """`min_elo_games` also picks the tours the all-time Elo may come from (`RatingRules.min_games`, stored on the
    players), so an admin change must be applied by a rebuild like the score values, not shown at once."""
    assert BY_KEY["score.min_elo_games"].effect == "rescore"
