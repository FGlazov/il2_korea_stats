"""The six cumulative medals read as two achievements (FR-WEB-26, doc 17, maintainer 2026-10-05: "rename the tour
variants slightly so this isn't so confusing"): the tour scope shows "Tour <name>", all time the career name, and the
admin renames the two variants independently."""

import pytest
from django.contrib.auth.models import User
from django.test import Client

from il2ks.core.achievements import ACHIEVEMENTS
from il2ks.db.models import PlayerSortie, SiteSettings
from il2ks.web import medals
from il2ks.web.achievement_config import AchievementConfig
from il2ks.web.admin_achievements import build_rows, parse_form
from tests.integration.test_achievement_config import configure, post_of
from tests.integration.test_achievements import pk
from tests.integration.test_achievements_all_time import fly, tours

pytestmark = pytest.mark.django_db

CAREER = {
    "career_kills": "Sky Hunter",
    "tank_buster": "Tank Buster",
    "flight_hours": "Hours Aloft",
    "shame_taxi": "Ramp Rash",
    "shame_friendly": "Wrong Team",
    "shame_strafed": "Sitting Duck",
}


def test_every_cumulative_medal_has_a_tour_variant_and_no_other_does() -> None:
    assert set(medals.TOUR_TEXTS) == {a.key for a in ACHIEVEMENTS if a.cumulative} == set(CAREER)
    for key, name in CAREER.items():
        assert str(medals.TEXTS[key][0]) == name
        assert str(medals.TOUR_TEXTS[key][0]) == f"Tour {name}"
        assert "in one tour" in str(medals.TOUR_TEXTS[key][1])
        assert "over your career" in str(medals.TEXTS[key][1])


def test_the_overview_and_holders_pages_name_the_variant_of_their_scope(client: Client) -> None:
    fly(0, kills_air=3)
    first = tours()[0]
    in_tour = client.get(f"/achievements/?tour={first.pk}").content.decode()
    all_time = client.get("/achievements/?tour=all").content.decode()
    for key, name in CAREER.items():
        assert f"Tour {name}" in in_tour, key
        assert f"Tour {name}" not in all_time, key
        assert name in all_time, key
        holders_tour = client.get(f"/achievements/{key}/?tour={first.pk}").content.decode()
        holders_all = client.get(f"/achievements/{key}/?tour=all").content.decode()
        assert f"Tour {name}: holders" in holders_tour, key
        assert f"{name}: holders" in holders_all, key
        assert f"Tour {name}" not in holders_all, key


def test_a_non_cumulative_medal_keeps_one_name(client: Client) -> None:
    fly(0, kills_air=3)
    first = tours()[0]
    in_tour = client.get(f"/achievements/?tour={first.pk}").content.decode()
    assert "Charmed Life" in in_tour
    assert "Charmed Life" in client.get("/achievements/?tour=all").content.decode()
    assert "Tour Charmed Life" not in in_tour


def test_the_profile_the_sortie_page_and_the_feed_name_the_variant_of_each_scope(client: Client) -> None:
    fly(0, kills_air=12)  # 12 kills: tier 2 in the tour (10), tier 1 all time (5)
    for other in range(2, 8):  # six pilots without kills: the tier is not too common for the feed
        fly(0, player=other)
    first = tours()[0]
    assert "Tour Sky Hunter" in client.get(f"/players/{pk(1)}/?tour={first.pk}").content.decode()
    all_time = client.get(f"/players/{pk(1)}/?tour=all").content.decode()
    assert "Sky Hunter" in all_time
    assert "Tour Sky Hunter" not in all_time
    sortie = PlayerSortie.objects.get(player_id=pk(1))
    page = client.get(f"/sorties/{sortie.pk}/").content.decode()
    assert "Tour Sky Hunter" in page  # the tour's tier ...
    assert page.count("Sky Hunter") > page.count("Tour Sky Hunter")  # ... and the career's: two achievements
    assert "Tour Sky Hunter" in client.get(f"/?tour={first.pk}").content.decode()


def test_the_admin_renames_each_variant_independently(client: Client) -> None:
    fly(0, kills_air=12)
    first = tours()[0]
    configure(
        names={"career_kills": {"en": "Lifetime Ace"}},
        tour_names={"career_kills": {"en": "Tour Ace"}},
        tour_descriptions={"career_kills": {"en": "Kills this tour"}},
    )
    in_tour = client.get(f"/achievements/?tour={first.pk}").content.decode()
    all_time = client.get("/achievements/?tour=all").content.decode()
    assert "Tour Ace" in in_tour
    assert "Kills this tour" in in_tour
    assert "Lifetime Ace" not in in_tour
    assert "Lifetime Ace" in all_time
    assert "Tour Ace" not in all_time
    assert "Tour Tank Buster" in in_tour  # the others keep the built-in words
    # an existing override of the career name only: the tour variant keeps its built-in name
    configure(names={"career_kills": {"en": "Lifetime Ace"}})
    assert "Tour Sky Hunter" in client.get(f"/achievements/?tour={first.pk}").content.decode()


def test_the_admin_form_saves_and_shows_both_variants() -> None:
    post = post_of(**{"name__career_kills__en": "Lifetime Ace", "tname__career_kills__en": "Tour Ace"})
    post["tdesc-career_kills-de"] = "Abschüsse in einer Tour"
    post["tname-life_kills-en"] = "ignored: no tour variant"
    config, errors, _ = parse_form(post, AchievementConfig())
    assert not errors
    assert config.names == {"career_kills": {"en": "Lifetime Ace"}}
    assert config.tour_names == {"career_kills": {"en": "Tour Ace"}}
    assert config.tour_descriptions == {"career_kills": {"de": "Abschüsse in einer Tour"}}
    assert AchievementConfig.from_row(SiteSettings(achievements=config.to_json())) == config
    rows = {r.key: r for r in build_rows(config)}
    assert rows["career_kills"].customised
    assert rows["career_kills"].tour_title == "Tour Sky Hunter"
    assert [t.name for t in rows["career_kills"].tour_texts if t.code == "en"] == ["Tour Ace"]
    assert rows["life_kills"].tour_texts == ()
    too_long = post_of(**{"tname__career_kills__en": "x" * 61})
    assert parse_form(too_long, AchievementConfig())[1]


def test_the_admin_page_renders_the_tour_fields(client: Client) -> None:
    client.force_login(User.objects.create_superuser("boss", "boss@example.org", "x"))
    body = client.get("/admin/achievements/").content.decode()
    assert 'name="tname-career_kills-en"' in body
    assert 'name="tdesc-shame_taxi-de"' in body
    assert 'name="tname-life_kills-en"' not in body


@pytest.mark.parametrize(
    ("language", "tour_name", "career_name"),
    [
        ("ru", "Небесный охотник (тур)", "Небесный охотник"),
        ("de", "Himmelsjäger (Tour)", "Himmelsjäger"),
        ("es", "Cazador del Cielo de la Temporada", "Cazador del Cielo"),
        ("fr", "Chasseur du Ciel (Tour)", "Chasseur du Ciel"),
        ("pt-br", "Caçador do Céu da Temporada", "Caçador do Céu"),
    ],
)
def test_the_variants_are_translated(client: Client, language: str, tour_name: str, career_name: str) -> None:
    fly(0, kills_air=3)
    first = tours()[0]
    headers = {"Accept-Language": language}
    assert tour_name in client.get(f"/achievements/?tour={first.pk}", headers=headers).content.decode()
    all_time = client.get("/achievements/?tour=all", headers=headers).content.decode()
    assert career_name in all_time
    assert tour_name not in all_time
