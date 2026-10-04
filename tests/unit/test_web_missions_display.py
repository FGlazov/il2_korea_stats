"""Display helpers of the mission pages and the coalition emblems (FR-WEB-1, FR-WEB-2, doc 15)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.template import Context, Template
from django.test import RequestFactory

from il2ks.db.models import BluforEmblem, RedforEmblem, SiteSettings
from il2ks.web import display

IMG = Path(__file__).resolve().parents[2] / "src" / "il2ks" / "web" / "static" / "il2ks" / "img"


@pytest.mark.parametrize(
    ("path", "title"),
    [
        (
            "Multiplayer/Dogfight\\Alonzo\\The_Sinuiju_Bridges_1951\\The_Sinuiju_Bridges_1951.msnbin",
            "The Sinuiju Bridges 1951",
        ),
        ("missions/korea_test", "korea test"),
        ("Operation_Dragnet_1951.msnbin", "Operation Dragnet 1951"),
        ("a\\b\\C__d  e.Mission", "C d e"),
        ("", "Mission"),
        ("dir/", "Mission"),
    ],
)
def test_mission_title_comes_from_the_file_name(path: str, title: str) -> None:
    assert display.mission_title(path) == title


@pytest.mark.parametrize(
    ("game_date", "game_time", "expected"),
    [("1951.9.15", "13:0:0", "1951-09-15 13:00"), ("1950.10.12", "9:5:30", "1950-10-12 09:05"), ("x", "y", "x y")],
)
def test_game_when(game_date: str, game_time: str, expected: str) -> None:
    assert display.game_when(game_date, game_time) == expected


def test_clock_since_is_time_into_the_mission() -> None:
    start = datetime(2026, 9, 19, 20, 0, tzinfo=UTC)
    assert display.clock_since(start + timedelta(seconds=754), start) == "12:34"
    assert display.clock_since(start + timedelta(hours=1, minutes=2, seconds=3), start) == "1:02:03"
    assert display.clock_since(start - timedelta(seconds=5), start) == "0:00"  # never negative
    assert display.clock_since(None, start) == display.DASH


def test_coalition_side_follows_the_missions_country_table() -> None:
    assert display.coalition_side(1, {"501": 1, "601": 2}) == "redfor"
    assert display.coalition_side(2, {"501": 1, "601": 2}) == "blufor"
    assert display.coalition_side(1, {"601": 1}) == "blufor"  # the countries decide, not the number
    assert display.coalition_side(2, None) == "blufor"  # no table: 1 = REDFOR, 2 = BLUFOR
    assert display.coalition_side(7, {}) is None
    assert display.coalition_side(None, {"501": 1}) is None


def test_every_emblem_choice_has_an_icon_file() -> None:
    for emblem in [*RedforEmblem.values, *BluforEmblem.values]:
        if emblem != "neutral":
            assert (IMG / "coalition" / "insignia" / f"{emblem}.svg").is_file(), emblem
    for side in ("redfor", "blufor"):
        assert (IMG / "coalition" / f"{side}.svg").is_file()


def test_coalition_icon_names() -> None:
    assert display.coalition_icon_name("redfor") == "coalition/redfor"
    assert display.coalition_icon_name("redfor", "neutral", "usaf") == "coalition/redfor"
    assert display.coalition_icon_name("redfor", "vvs", "usaf") == "coalition/insignia/vvs"
    assert display.coalition_icon_name("blufor", "vvs", "usaf") == "coalition/insignia/usaf"
    assert display.coalition_icon_name(None, "vvs", "usaf") == ""


@pytest.mark.parametrize("emblem", [e for e in [*RedforEmblem.values, *BluforEmblem.values] if e != "neutral"])
def test_insignia_are_plain_self_contained_svgs(emblem: str) -> None:
    svg = (IMG / "coalition" / "insignia" / f"{emblem}.svg").read_text(encoding="utf-8")
    assert svg.startswith("<svg")
    assert 'viewBox="0 0 24 24"' in svg
    assert "<script" not in svg
    assert "<image" not in svg
    assert "href" not in svg  # shapes only, no copied artwork


def render(source: str, site: SiteSettings) -> str:
    context = Context({"request": RequestFactory().get("/"), "site": site})
    return Template("{% load il2ks %}" + source).render(context)


def test_coalition_badge_and_icon_use_the_chosen_emblem() -> None:
    neutral = render("{% coalition_badge 501 %}{% coalition_icon 601 %}", SiteSettings())
    chosen = render(
        "{% coalition_badge 501 %}{% coalition_icon 601 %}", SiteSettings(redfor_emblem="vvs", blufor_emblem="un")
    )

    assert "tint--redfor" in neutral
    assert "M12 17.75l-6.172 3.245" in neutral  # the neutral star outline
    assert 'fill="#c8312b"' in chosen  # VVS star in the REDFOR badge
    assert 'fill="#4f8fdc"' in chosen  # UN roundel for BLUFOR
    assert 'fill="#c8312b"' not in neutral


def test_an_unknown_emblem_falls_back_to_the_neutral_one() -> None:
    html = render("{% coalition_icon 501 %}", SiteSettings(redfor_emblem="no-such-emblem"))

    assert "<svg" in html
    assert "#c8312b" not in html


def test_winner_badge() -> None:
    site = SiteSettings(blufor_name="Allies")
    won = SimpleNamespace(winning_coalition=2, countries={"501": 1, "601": 2})
    unknown = SimpleNamespace(winning_coalition=None, countries={})

    context = Context({"request": RequestFactory().get("/"), "site": site, "won": won, "unknown": unknown})
    rendered = Template("{% load il2ks %}{% winner_badge won %}|{% winner_badge unknown %}").render(context)
    winner, nobody = rendered.split("|")
    assert "Allies" in winner
    assert "badge--blufor" in winner
    assert display.DASH in nobody
    assert "muted" in nobody
