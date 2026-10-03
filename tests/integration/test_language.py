"""Language switching, its interplay with HTTP caching, and object names in the viewer's language (TD-24, TD-28)."""

import re

import pytest
from django.http import HttpResponse
from django.template import Context, Template
from django.test import Client, RequestFactory
from django.utils import translation

from il2ks.db.models import GameObject, Player
from il2ks.db.site import bump_data_version
from il2ks.web.object_names import name_of
from tests.factories import account, mission, save, sortie

pytestmark = pytest.mark.django_db

PLAYERS = {"en": "Players", "ru": "Игроки", "de": "Spieler", "es": "Jugadores", "fr": "Joueurs", "pt-br": "Jogadores"}
COOKIE = "django_language"


def switch(client: Client, language: str, next_url: str = "/") -> HttpResponse:
    return client.get("/language/", {"language": language, "next": next_url})


def nav_text(response: HttpResponse) -> str:
    return response.content.decode()


# --- the switcher -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(("code", "word"), PLAYERS.items())
def test_switching_the_language_changes_the_rendered_page(client: Client, code: str, word: str) -> None:
    response = switch(client, code, "/")

    assert response.status_code == 302
    assert response["Location"] == "/"
    page = client.get("/")
    assert page.status_code == 200
    assert f">{word}</a>" in nav_text(page)
    assert f'<html lang="{code}">' in nav_text(page)


def test_the_choice_is_a_cookie_that_lasts_and_is_never_cached(client: Client) -> None:
    response = switch(client, "de")

    cookie = response.cookies[COOKIE]
    assert cookie.value == "de"
    assert int(cookie["max-age"]) >= 60 * 60 * 24 * 30
    assert cookie["samesite"] == "Lax"
    assert "no-cache" in response["Cache-Control"] or "no-store" in response["Cache-Control"]
    assert not response.has_header("ETag")


def test_an_unknown_language_is_ignored_and_next_stays_local(client: Client) -> None:
    response = switch(client, "tlh", "https://evil.example/phish")

    assert COOKIE not in response.cookies
    assert response["Location"] == "/"  # an off-site `next` is replaced, never followed
    assert switch(client, "de", "/players/?q=x")["Location"] == "/players/?q=x"
    assert switch(client, "de", "//evil.example/")["Location"] == "/"


def test_the_switcher_only_answers_get_and_head(client: Client) -> None:
    assert client.post("/language/", {"language": "de"}).status_code == 405
    assert client.head("/language/", {"language": "de"}).status_code == 302


def test_every_page_offers_all_languages_and_returns_to_it(client: Client) -> None:
    page = nav_text(client.get("/players/?q=ab"))

    for name in ("English", "Русский", "Deutsch", "Español", "Français", "Português brasileiro"):
        assert name in page
    assert re.search(r'href="/language/\?language=de&amp;next=%2Fplayers%2F%3Fq%3Dab"', page)


def test_the_menu_shows_the_current_language_in_its_own_language(client: Client) -> None:
    switch(client, "fr")

    page = nav_text(client.get("/"))

    assert re.search(r"<summary>\s*Français\s*</summary>", page)


def test_accept_language_is_used_until_a_choice_is_made(client: Client) -> None:
    german = client.get("/", headers={"Accept-Language": "de-DE,de;q=0.9"})
    assert ">Spieler</a>" in nav_text(german)

    switch(client, "es")
    spanish = client.get("/", headers={"Accept-Language": "de-DE,de;q=0.9"})
    assert ">Jugadores</a>" in nav_text(spanish)  # the cookie beats the browser's header


def test_html_and_pages_stay_english_for_unsupported_languages(client: Client) -> None:
    page = client.get("/", headers={"Accept-Language": "ko-KR,ko;q=0.9"})

    assert ">Players</a>" in nav_text(page)


# --- caching (TD-28) --------------------------------------------------------------------------------------------------
def etag_for(client: Client, language: str | None) -> str:
    if language is not None:
        switch(client, language)
    return client.get("/")["ETag"]


def test_the_etag_differs_per_language() -> None:
    tags = {code: etag_for(Client(), code) for code in PLAYERS}

    assert len(set(tags.values())) == len(PLAYERS)


def test_a_cached_page_of_another_language_is_never_answered_304() -> None:
    english = Client()
    english_etag = english.get("/")["ETag"]
    german = Client()
    switch(german, "de")

    response = german.get("/", headers={"If-None-Match": english_etag})

    assert response.status_code == 200
    assert ">Spieler</a>" in nav_text(response)
    assert german.get("/", headers={"If-None-Match": response["ETag"]}).status_code == 304


def test_responses_vary_on_the_language_cookie_and_the_header(client: Client) -> None:
    assert "Cookie" not in client.get("/")["Vary"]  # nobody chose a language: nothing to vary on
    switch(client, "de")  # sets the cookie
    vary = {v.strip() for v in client.get("/")["Vary"].split(",")}

    assert {"Accept-Language", "Cookie"} <= vary


def test_accept_language_also_changes_the_etag(client: Client) -> None:
    plain = client.get("/")["ETag"]

    assert client.get("/", headers={"Accept-Language": "ru"})["ETag"] != plain


def test_a_data_change_still_revalidates_in_every_language(client: Client) -> None:
    switch(client, "de")
    etag = client.get("/")["ETag"]
    bump_data_version()

    assert client.get("/", headers={"If-None-Match": etag}).status_code == 200


# --- object names in the viewer's language ----------------------------------------------------------------------------
def fence() -> GameObject:
    return GameObject(log_name="Fence wire 100m", display_name="Fence wire 100m", cls="static", is_known=True)


def test_name_fallback_chain() -> None:
    translated = fence()
    assert name_of(translated, "de") == "Drahtzaun 100 m"  # 1. the viewer's language
    assert name_of(translated, "pt-br") == "Cerca de arame 100 m"
    assert name_of(translated, "en") == "Fence wire 100m"  # 2. the shipped English default
    assert name_of(translated, "ko") == "Fence wire 100m"

    overridden = fence()
    overridden.display_name, overridden.name_overridden = "Perimeter wire", True
    assert name_of(overridden, "de") == "Perimeter wire"  # an admin's override wins in every language
    assert name_of(overridden, "en") == "Perimeter wire"

    type_name = GameObject(log_name="F-86A-5", display_name="F-86A-5", cls="fighter")
    assert name_of(type_name, "de") == "F-86A-5"  # no translation row: English

    unknown = GameObject(log_name="Made-Up-1", display_name="Made-Up-1", cls="unknown", is_known=False)
    assert name_of(unknown, "de") == "Made-Up-1"

    nameless = GameObject(log_name="Raw_Log_Name", display_name="", cls="unknown")
    assert name_of(nameless, "de") == "Raw_Log_Name"  # 4. the raw log name


def test_a_page_shows_object_names_in_the_viewers_language(client: Client) -> None:
    save(mission((sortie(0, 1, name="Maverick", aircraft_type="Yak-9P"),)))
    GameObject.objects.filter(log_name="Yak-9P").update(display_name="Yak-9P")
    url = f"/players/{Player.objects.get(account_uuid=account(1)).pk}/"

    assert ">Yak-9P</a>" in nav_text(client.get(url))
    switch(client, "ru")
    assert ">Як-9П</a>" in nav_text(client.get(url))
    GameObject.objects.filter(log_name="Yak-9P").update(display_name="Yak (club name)", name_overridden=True)
    bump_data_version()
    assert ">Yak (club name)</a>" in nav_text(client.get(url))


def test_name_of_tolerates_missing_objects() -> None:
    assert name_of(None, "de") == ""
    assert name_of("plain text", "de") == "plain text"


def test_the_object_name_filter_follows_the_active_language() -> None:
    template = Template("{% load il2ks %}{{ obj|object_name }}")
    request = RequestFactory().get("/")
    for code, expected in (
        ("de", "Drahtzaun 100 m"),
        ("ru", "Проволочное ограждение 100 м"),
        ("en", "Fence wire 100m"),
    ):
        with translation.override(code):
            assert template.render(Context({"obj": fence(), "request": request})) == expected


def test_the_mission_page_translates_object_names(client: Client) -> None:
    """Pages showing an aircraft go through `object_name` (TD-24), not `.display_name`."""
    saved = save(mission((sortie(0, 1, name="Maverick", aircraft_type="Yak-9P"),)))
    GameObject.objects.filter(log_name="Yak-9P").update(display_name="Yak-9P")
    switch(client, "ru")
    assert "Як-9П" in nav_text(client.get(f"/missions/{saved.pk}/"))
