"""Weapon-mod names follow the viewer's language on the sortie page and the aircraft page (TD-24): the maintainer's
rule (2026-10-05) is that mod names are dimensions (translated), mission names are facts (never translated)."""

import re

import pytest
from django.test import Client

from il2ks.db.models import PlayerSortie
from tests.factories import mission, save, sortie
from tests.integration.test_aircraft_mods import detail, history

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("list_every_row")]


def switch(client: Client, language: str) -> None:
    client.get("/language/", {"language": language, "next": "/"})


def test_the_sortie_page_shows_mod_names_in_the_viewers_language(client: Client) -> None:
    save(mission((sortie(0, 1, weapon_mods=0b101011), sortie(1, 2, weapon_mods=1 | 1 << 9))))
    first, second = (PlayerSortie.objects.order_by("pk")[i].pk for i in (0, 1))
    switch(client, "ru")

    body = client.get(f"/sorties/{first}/").content.decode()
    assert "Противоперегрузочный костюм" in body
    for english in ("NR-23 cannons", "Anti-G suit", "Warning system", "Unknown modification"):
        assert english not in body
    switch(client, "de")
    assert "Anti-g-Anzug" in client.get(f"/sorties/{first}/").content.decode()
    switch(client, "en")
    assert "Unknown modification (id 9)" in client.get(f"/sorties/{second}/").content.decode()  # raw id kept


def test_the_aircraft_page_filter_and_table_follow_the_language(client: Client) -> None:
    history()
    url = f"{detail()}?tour=all"
    switch(client, "de")

    body = client.get(url).content.decode()
    assert re.search(r'aria-label="[^"]*Anti-g-Anzug[^"]*"', body)
    assert 'class="muted">Anti-g-Anzug</span>' in body
    assert "Anti-G suit" not in body
    labels = [r.label for r in client.get(f"{url}&mod5=with").context["mod_sets"]]
    assert "Anti-g-Anzug" in labels
    assert any(label.endswith(" + Anti-g-Anzug") for label in labels)  # the joiner stays, the parts are translated

    switch(client, "en")
    assert "Anti-G suit" in client.get(url).content.decode()


def test_a_mod_set_with_an_unlisted_id_keeps_its_raw_label(client: Client) -> None:
    history()
    save(mission((sortie(0, 1, aircraft_type="MiG-15bis", weapon_mods=1 | 1 << 20),)))
    switch(client, "ru")

    labels = [r.label for r in client.get(f"{detail()}?tour=all").context["mod_sets"]]
    assert "#20" in labels
