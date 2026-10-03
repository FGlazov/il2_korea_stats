"""The two journeys the maintainer asked for (doc 08, "Playwright end-to-end tests on key flows"):

(a) a player looks up his own sortie, (b) someone opens a mission, finds himself and looks at a sortie from there.

Written against what a visitor sees (roles, labels, words) for pages that were still being built when the harness
landed, so the exact words are a best guess: when the pages merge, remove `PAGES_PENDING`, run the tests, and adjust
the labels that differ (each step says what it expects in plain words). Data: `tests/e2e/world.py`.
"""

import re

from playwright.sync_api import Page, expect

from tests.e2e.helpers import (
    PAGES_PENDING,
    expect_heading,
    expect_same_document,
    header_search,
    link_or_button,
    main_region,
    mark_page,
    sortie_links,
)
from tests.e2e.world import World

pytestmark = PAGES_PENDING


def test_a_player_finds_his_own_sortie(page: Page, world: World) -> None:
    """Home -> search box -> live results -> profile -> a recent sortie -> aircraft, outcome and timeline."""
    # 1. the start page has a search box; typing part of the name and pressing Enter shows matching players
    page.goto("/")
    header_search(page).fill("Ace")
    header_search(page).press("Enter")
    expect(page).to_have_url(re.compile(r"/players/\?q=Ace"))
    main = main_region(page)
    expect(main.get_by_role("link", name=world.ace)).to_be_visible()
    expect(main.get_by_role("link", name=world.acey)).to_be_visible()  # same prefix: two hits

    # 2. refining the search updates the results in place (htmx) and the address, without reloading the page
    mark_page(page)
    box = main.get_by_role("searchbox")
    box.press_sequentially(" P")  # "Ace P": only one of the two is left
    expect(main.get_by_role("link", name=world.acey)).to_have_count(0)
    expect(main.get_by_role("link", name=world.ace)).to_be_visible()
    expect(page).to_have_url(re.compile(r"[?&]q=Ace(\+|%20)P"))
    expect_same_document(page)

    # 3. the profile shows who he is and his recent sorties
    main.get_by_role("link", name=world.ace).click()
    expect(page).to_have_url(re.compile(rf"/players/{world.ace_pk}/$"))
    expect_heading(page, world.ace, level=1)
    expect(main_region(page).get_by_text(world.ace_aircraft).first).to_be_visible()

    # 4. a recent sortie opens its detail page: aircraft, outcome, timeline
    sortie_links(page).first.click()
    expect(page).to_have_url(re.compile(r"/sorties/\d+/$"))
    detail = main_region(page)
    expect(detail.get_by_text(world.ace_aircraft).first).to_be_visible()
    expect(detail.get_by_text(re.compile("landed", re.IGNORECASE)).first).to_be_visible()
    expect_heading(page, re.compile("timeline", re.IGNORECASE))
    expect(detail.get_by_text(re.compile("shot down", re.IGNORECASE)).first).to_be_visible()  # the timeline's kill


def test_a_player_sees_a_bailout_and_who_shot_him_down(page: Page, world: World) -> None:
    """Bob bailed out of his MiG after Charlie shot him down: the sortie page says both."""
    page.goto(f"/players/?q={world.bob}")
    main_region(page).get_by_role("link", name=world.bob).click()
    sortie_links(page).first.click()

    detail = main_region(page)
    expect(detail.get_by_text(re.compile("bailed out", re.IGNORECASE)).first).to_be_visible()
    expect(detail.get_by_text(re.compile("shot down by", re.IGNORECASE)).first).to_be_visible()
    expect(detail.get_by_role("link", name=world.charlie)).to_be_visible()


def test_someone_finds_himself_in_a_mission_and_opens_his_sortie(page: Page, world: World) -> None:
    """Mission list -> mission -> coalition table -> his sortie -> "shot down by" and the kills."""
    # 1. the list shows missions, newest first: the story's mission is the newest
    page.goto("/")
    link_or_button(page, "Browse missions").click()
    expect(page).to_have_url(re.compile(r"/missions/$"))
    page.get_by_role("main").get_by_role("link", name=re.compile(r"2026")).first.click()
    expect(page).to_have_url(re.compile(rf"/missions/{world.featured_mission_pk}/$"))

    # 2. both coalitions are listed with their pilots; Delta is on the other side from Ace
    detail = main_region(page)
    for name in (world.ace, world.bob, world.charlie, world.delta):
        expect(detail.get_by_role("link", name=name)).to_be_visible()
    expect(detail.get_by_text(world.delta_aircraft).first).to_be_visible()

    # 3. Delta's row leads to his sortie of this mission
    row = detail.get_by_role("row").filter(has=page.get_by_role("link", name=world.delta))
    row.get_by_role("link").and_(page.locator("a[href^='/sorties/']")).first.click()
    expect(page).to_have_url(re.compile(rf"/sorties/{world.delta_sortie_pk}/$"))

    # 4. he was shot down, by Ace: the sortie names the killer, and Ace's page is one click away
    sortie = main_region(page)
    expect(sortie.get_by_text(re.compile("shot down by", re.IGNORECASE)).first).to_be_visible()
    sortie.get_by_role("link", name=world.ace).first.click()
    expect(page).to_have_url(re.compile(rf"/players/{world.ace_pk}/$"))


def test_a_sortie_lists_the_kills_of_the_player(page: Page, world: World) -> None:
    """Ace shot down Delta (and got an assist): his sortie lists his kills, with Delta's name and aircraft."""
    page.goto(f"/sorties/{world.ace_sortie_pk}/")

    kills = main_region(page)
    expect_heading(page, re.compile("kills", re.IGNORECASE))
    expect(kills.get_by_role("link", name=world.delta)).to_be_visible()
    expect(kills.get_by_text(world.delta_aircraft).first).to_be_visible()


def test_a_players_sortie_list_sorts_in_place(page: Page, world: World) -> None:
    """Ace flew nine missions (the story plus every seventh filler): the list sorts through htmx and pushes the URL."""
    page.goto(f"/players/{world.ace_pk}/sorties/")
    mark_page(page)
    assert sortie_links(page).count() >= 2

    page.get_by_role("columnheader").get_by_role("link").first.click()

    expect(page).to_have_url(re.compile(r"[?&]sort="))
    expect_same_document(page)
