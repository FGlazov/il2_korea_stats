"""Admin-configurable quips, the pure part (FR-WEB-23): the effective list, the pick, tolerant parsing."""

import pytest

from il2ks.web import flavor, quips
from il2ks.web.quips import CustomQuip, QuipConfig

EN = "en"


def test_every_spot_has_a_description_for_the_admin_page() -> None:
    assert set(flavor.SPOT_DESCRIPTIONS) == set(flavor.SPOTS)


def test_no_admin_choices_pick_exactly_what_flavor_pick_picks() -> None:
    config = QuipConfig()
    for spot in flavor.SPOTS:
        for seed in range(25):
            assert quips.pick(config, spot, seed, EN) == str(flavor.pick(spot, seed))


def test_the_global_switch_and_the_off_mode_give_no_quip() -> None:
    assert quips.pick(QuipConfig(enabled=False), "top_pilot", 1, EN) == ""
    assert quips.pick(QuipConfig(modes={"top_pilot": "off"}), "top_pilot", 1, EN) == ""
    assert quips.pick(QuipConfig(modes={"top_pilot": "off"}), "nobody_scored", 1, EN) != ""


def test_a_hidden_default_is_never_picked_and_the_rest_stay() -> None:
    first = quips.default_keys("top_pilot")[0]
    config = QuipConfig(hidden={"top_pilot": frozenset({first})})
    chosen = {quips.pick(config, "top_pilot", seed, EN) for seed in range(300)}
    assert first not in chosen
    assert len(chosen) == len(flavor.SPOTS["top_pilot"]) - 1


def test_the_key_of_a_default_is_its_english_text() -> None:
    assert quips.default_keys("shame_clean")[0] == "A clean sheet so far. The runway thanks you."


def test_an_orphaned_hide_does_nothing() -> None:
    config = QuipConfig(hidden={"top_pilot": frozenset({"A line a later release reworded"})})
    assert quips.variants(config, "top_pilot", EN) == [str(v) for v in flavor.SPOTS["top_pilot"]]


def test_modes_combine_defaults_and_custom() -> None:
    mine = (CustomQuip("top_pilot", "Mine"),)
    defaults = [str(v) for v in flavor.SPOTS["top_pilot"]]
    assert quips.variants(QuipConfig(custom=mine), "top_pilot", EN) == defaults
    both = QuipConfig(modes={"top_pilot": "defaults_and_custom"}, custom=mine)
    assert quips.variants(both, "top_pilot", EN) == [*defaults, "Mine"]
    only = QuipConfig(modes={"top_pilot": "custom_only"}, custom=mine)
    assert quips.variants(only, "top_pilot", EN) == ["Mine"]
    assert quips.pick(only, "top_pilot", 5, EN) == "Mine"


def test_custom_only_without_a_quip_for_the_language_gives_nothing() -> None:
    config = QuipConfig(modes={"top_pilot": "custom_only"}, custom=(CustomQuip("top_pilot", "Nur Deutsch", "de"),))
    assert quips.pick(config, "top_pilot", 1, "de") == "Nur Deutsch"
    assert quips.pick(config, "top_pilot", 1, EN) == ""


def test_a_blank_language_is_every_language_and_a_code_matches_its_region_variants() -> None:
    config = QuipConfig(
        modes={"top_pilot": "custom_only"},
        custom=(CustomQuip("top_pilot", "All"), CustomQuip("top_pilot", "Portuguese", "pt-br")),
    )
    assert quips.variants(config, "top_pilot", "de") == ["All"]
    assert quips.variants(config, "top_pilot", "pt-br") == ["All", "Portuguese"]


def test_a_disabled_custom_quip_is_skipped() -> None:
    config = QuipConfig(modes={"top_pilot": "custom_only"}, custom=(CustomQuip("top_pilot", "Off", enabled=False),))
    assert quips.variants(config, "top_pilot", EN) == []


def test_unknown_spot_fails_loudly() -> None:
    with pytest.raises(KeyError):
        quips.pick(QuipConfig(), "no_such_spot", 1, EN)


def test_the_json_round_trip_keeps_only_what_differs() -> None:
    config = QuipConfig(
        modes={"top_pilot": "custom_only", "tour_empty": "defaults"},
        hidden={"top_pilot": frozenset({"x"})},
        custom=(CustomQuip("top_pilot", "Mine", "de", False),),
    )
    stored = config.to_json()
    assert stored["modes"] == {"top_pilot": "custom_only"}
    again = QuipConfig.from_row(True, stored)
    assert again.mode("top_pilot") == "custom_only"
    assert again.hidden == {"top_pilot": frozenset({"x"})}
    assert again.custom == (CustomQuip("top_pilot", "Mine", "de", False),)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "text",
        [],
        5,
        {"modes": [], "hidden": 3, "custom": "x"},
        {"custom": [1, {"spot": 4}, {"spot": "nope", "text": "t"}]},
    ],
)
def test_a_malformed_row_never_breaks_a_page(raw: object) -> None:
    config = QuipConfig.from_row(True, raw)
    assert config.custom == ()
    assert quips.variants(config, "top_pilot", EN)


def test_a_stored_text_is_cut_to_the_limit() -> None:
    raw = {"custom": [{"spot": "top_pilot", "text": "x" * 500}]}
    assert len(QuipConfig.from_row(True, raw).custom[0].text) == quips.MAX_LEN
