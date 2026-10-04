"""The key journeys (doc 08, "Playwright end-to-end tests on key flows"), told the way a visitor does them:

1. a player searches his name, opens his profile and his latest sortie, and follows a kill to the enemy's sortie, which
   shows the reverse relation (the kill list of one is the "Shot down by" of the other);
2. someone finds himself in a mission and opens his sortie from there;
3. someone browses several missions (list, mission, back, next page, another mission) and opens sorties from them.

Written against roles, names and data (pilots, aircraft, counts, URLs), never against times (they render in the viewer's
timezone) or flavor sentences (they vary), and never against a table row's styling (a row may be one big link): links
are clicked by role and name. Data: `tests/e2e/world.py`.
"""

import re

from playwright.sync_api import Locator, Page, expect

from tests.e2e.helpers import (
    expect_heading,
    expect_same_document,
    header_search,
    link_or_button,
    main_region,
    mark_page,
    sortie_links,
)
from tests.e2e.world import World


def kill_table(page: Page, victim_heading: str = "Victim") -> Locator:
    """The kill list of a sortie page (its victim column is called `victim_heading`)."""
    return main_region(page).get_by_role("table").filter(has=page.get_by_role("columnheader", name=victim_heading))


def shot_down_by_table(page: Page) -> Locator:
    """The "Shot down by" list of a sortie page (the one with an "Attacker" column)."""
    return main_region(page).get_by_role("table").filter(has=page.get_by_role("columnheader", name="Attacker"))


def search_and_open_profile(page: Page, name: str) -> None:
    """Home -> the header search -> the player's row -> his profile."""
    page.goto("/")
    header_search(page).fill(name)
    header_search(page).press("Enter")
    expect(page).to_have_url(re.compile(r"/players/\?q="))
    main_region(page).get_by_role("link", name=name).click()
    expect(page).to_have_url(re.compile(r"/players/\d+/$"))
    expect_heading(page, name, level=1)


def open_latest_sortie(page: Page) -> None:
    """From a profile: the first sortie of the recent-sorties block (newest first). Scoped to that block: the medal row
    above it links to the sortie each medal was earned in, which is not the latest one."""
    page.locator("#recent").locator("a[href^='/sorties/']").first.click()
    expect(page).to_have_url(re.compile(r"/sorties/\d+/$"))


def open_mission_row(page: Page, row: int) -> None:
    """Open the `row`-th mission (1 = the first) of the mission list through its named link."""
    main_region(page).get_by_role("row").nth(row).get_by_role("link").first.click()
    expect(page).to_have_url(re.compile(r"/missions/\d+/$"))


def open_sortie_of(page: Page, name: str) -> None:
    """On a mission page: the "Sortie details" link of the row where `name` flew."""
    row = (
        main_region(page)
        .get_by_role("row")
        .filter(has=page.get_by_role("link", name=name, exact=True))
        .filter(has=page.get_by_role("link", name="Sortie details"))
    )
    row.first.get_by_role("link", name="Sortie details").click()
    expect(page).to_have_url(re.compile(r"/sorties/\d+/$"))


def test_a_player_finds_his_own_sortie(page: Page, world: World) -> None:
    """Home -> search box -> live results -> profile -> latest sortie -> aircraft, outcome, kills."""
    # 1. the header search finds both players with that prefix; refining it updates the results in place (htmx)
    page.goto("/")
    header_search(page).fill("Ace")
    header_search(page).press("Enter")
    expect(page).to_have_url(re.compile(r"/players/\?q=Ace"))
    main = main_region(page)
    expect(main.get_by_role("link", name=world.ace)).to_be_visible()
    expect(main.get_by_role("link", name=world.acey)).to_be_visible()

    mark_page(page)
    box = main.get_by_role("searchbox")
    box.press("End")
    box.press_sequentially(" P")  # "Ace P": only one of the two is left
    expect(main.get_by_role("link", name=world.acey)).to_have_count(0)
    expect(main.get_by_role("link", name=world.ace)).to_be_visible()
    expect(page).to_have_url(re.compile(r"[?&]q=Ace(\+|%20)P"))
    expect_same_document(page)

    # 2. the profile shows who he is and what he flew
    main.get_by_role("link", name=world.ace).click()
    expect(page).to_have_url(re.compile(rf"/players/{world.ace_pk}/$"))
    expect_heading(page, world.ace, level=1)
    expect(main_region(page).get_by_text(world.ace_aircraft).first).to_be_visible()

    # 3. his latest sortie is the story's: aircraft, a landing, two air kills with Delta among the victims
    open_latest_sortie(page)
    expect(page).to_have_url(re.compile(rf"/sorties/{world.ace_sortie_pk}/$"))
    detail = main_region(page)
    expect_heading(page, world.ace, level=1)
    expect(detail.get_by_text(world.ace_aircraft).first).to_be_visible()
    expect(detail.get_by_text(re.compile("landed", re.IGNORECASE)).first).to_be_visible()
    expect(detail.get_by_role("heading", name="Air kills: 2")).to_be_visible()
    expect(kill_table(page).get_by_role("link", name=world.delta)).to_be_visible()


def test_a_player_follows_his_kill_to_the_enemy_sortie_and_sees_the_reverse(page: Page, world: World) -> None:
    """Ace's latest sortie lists Delta as a victim; Delta's sortie lists Ace under "Shot down by" and links back."""
    search_and_open_profile(page, world.ace)
    open_latest_sortie(page)
    expect(page).to_have_url(re.compile(rf"/sorties/{world.ace_sortie_pk}/$"))

    # the victim's aircraft links to the victim's sortie
    kill_table(page).get_by_role("row").filter(has=page.get_by_role("link", name=world.delta)).get_by_role(
        "link", name=world.delta_aircraft
    ).click()
    expect(page).to_have_url(re.compile(rf"/sorties/{world.delta_sortie_pk}/$"))

    # Delta's sortie: his name, his aircraft, and Ace (with his aircraft) as the attacker
    expect_heading(page, world.delta, level=1)
    expect(main_region(page).get_by_text(world.delta_aircraft).first).to_be_visible()
    expect_heading(page, "Shot down by", level=2)
    attackers = shot_down_by_table(page)
    expect(attackers.get_by_role("link", name=world.ace)).to_be_visible()
    # ... and the attacker's aircraft leads back to the sortie we came from
    attackers.get_by_role("link", name=world.ace_aircraft).click()
    expect(page).to_have_url(re.compile(rf"/sorties/{world.ace_sortie_pk}/$"))
    expect(kill_table(page).get_by_role("link", name=world.delta)).to_be_visible()


def test_a_player_sees_a_bailout_and_who_shot_him_down(page: Page, world: World) -> None:
    """Bob bailed out of his MiG after Charlie shot him down; Charlie's sortie lists Bob among his kills."""
    search_and_open_profile(page, world.bob)
    open_latest_sortie(page)

    detail = main_region(page)
    expect_heading(page, world.bob, level=1)
    expect(detail.get_by_text(re.compile("bailed out", re.IGNORECASE)).first).to_be_visible()
    expect_heading(page, "Shot down by", level=2)
    attackers = shot_down_by_table(page)
    expect(attackers.get_by_role("link", name=world.charlie)).to_be_visible()

    # the attacker's aircraft leads to Charlie's sortie, where Bob is a kill (he flew an F-86A-5: 1 air, 3 ground)
    attackers.get_by_role("link", name=world.delta_aircraft).click()
    expect_heading(page, world.charlie, level=1)
    expect(main_region(page).get_by_role("heading", name="Air kills: 1")).to_be_visible()
    expect(main_region(page).get_by_role("heading", name="Ground kills: 3")).to_be_visible()
    victims = kill_table(page)
    expect(victims.get_by_role("link", name=world.bob)).to_be_visible()
    expect(victims.get_by_role("link", name=world.bob_aircraft)).to_be_visible()


def test_someone_finds_himself_in_a_mission_and_opens_his_sortie(page: Page, world: World) -> None:
    """Mission list -> the newest mission -> his row -> his sortie -> "shot down by" -> the killer's profile."""
    # 1. the list is newest first: the first mission is the story's
    page.goto("/")
    link_or_button(page, "All missions").click()
    expect(page).to_have_url(re.compile(r"/missions/[?]tour=all$"))
    open_mission_row(page, 1)
    expect(page).to_have_url(re.compile(rf"/missions/{world.featured_mission_pk}/$"))

    # 2. both coalitions are listed with their pilots and aircraft
    detail = main_region(page)
    for name in (world.ace, world.bob, world.charlie, world.delta):
        expect(detail.get_by_role("link", name=name).first).to_be_visible()
    expect(detail.get_by_text(world.delta_aircraft).first).to_be_visible()

    # 3. Delta's row leads to his sortie of this mission
    open_sortie_of(page, world.delta)
    expect(page).to_have_url(re.compile(rf"/sorties/{world.delta_sortie_pk}/$"))

    # 4. he was shot down, by Ace: the sortie names the killer, and Ace's page is one click away
    expect_heading(page, "Shot down by", level=2)
    shot_down_by_table(page).get_by_role("link", name=world.ace, exact=True).click()
    expect(page).to_have_url(re.compile(rf"/players/{world.ace_pk}/$"))


def test_someone_browses_several_missions_and_opens_sorties(page: Page, world: World) -> None:
    """List -> mission -> back -> next page (in place) -> another mission -> sorties; every page tells its own facts."""
    page.goto("/missions/?tour=all")
    expect(page.get_by_role("navigation", name="Pagination")).to_contain_text(f"of {world.mission_count + 1}")

    # 1. the newest mission, then back to the list
    open_mission_row(page, 1)
    first_url = page.url
    expect(page).to_have_url(re.compile(rf"/missions/{world.featured_mission_pk}/$"))
    expect(main_region(page).get_by_role("link", name=world.delta, exact=True).first).to_be_visible()
    page.go_back()
    expect(page).to_have_url(re.compile(r"/missions/[?]tour=all$"))

    # 2. page two of the list (htmx), then an older mission from it
    mark_page(page)
    page.get_by_role("link", name="Next").click()
    expect(page).to_have_url(re.compile(r"[?&]page=2"))
    expect_same_document(page)
    open_mission_row(page, 1)
    second_url = page.url
    assert second_url != first_url
    detail = main_region(page)
    filler_link = detail.get_by_role("link", name=re.compile(r"^Filler Pilot \d+$")).first
    expect(filler_link).to_be_visible()
    expect(detail.get_by_text("F-86A-5").first).to_be_visible()
    assert detail.get_by_role("link", name=world.delta, exact=True).count() == 0  # not the story's mission

    # 3. his sortie in the older mission: the filler pilot flew an F-86A-5
    filler_name = filler_link.inner_text()
    open_sortie_of(page, filler_name)
    expect_heading(page, filler_name, level=1)
    expect(main_region(page).get_by_text("F-86A-5").first).to_be_visible()
    page.go_back()
    expect(page).to_have_url(second_url)

    # 4. the other side of that mission: Ace (every seventh) or Echo, in a MiG-15bis
    opponent = main_region(page).get_by_role("link", name=re.compile(rf"^({world.ace}|{world.echo})$")).first
    opponent_name = opponent.inner_text()
    open_sortie_of(page, opponent_name)
    expect_heading(page, opponent_name, level=1)
    expect(main_region(page).get_by_text(world.ace_aircraft).first).to_be_visible()

    # 5. back to the very first mission: the story is still there
    page.goto(first_url)
    open_sortie_of(page, world.ace)
    expect(page).to_have_url(re.compile(rf"/sorties/{world.ace_sortie_pk}/$"))
    expect(main_region(page).get_by_role("heading", name="Air kills: 2")).to_be_visible()


def test_a_sortie_lists_the_kills_of_the_player(page: Page, world: World) -> None:
    """Ace shot down Delta (and got an assist): his sortie lists his kills, with Delta's name and aircraft."""
    page.goto(f"/sorties/{world.ace_sortie_pk}/")

    expect_heading(page, re.compile("kills", re.IGNORECASE))
    expect(kill_table(page).get_by_role("link", name=world.delta)).to_be_visible()
    expect(kill_table(page).get_by_role("link", name=world.delta_aircraft)).to_be_visible()


def test_a_players_sortie_list_sorts_in_place(page: Page, world: World) -> None:
    """Ace flew nine missions (the story plus every seventh filler): the list sorts through htmx and pushes the URL."""
    page.goto(f"/players/{world.ace_pk}/sorties/")
    mark_page(page)
    assert sortie_links(page).count() >= 2

    page.get_by_role("columnheader").get_by_role("link").first.click()

    expect(page).to_have_url(re.compile(r"[?&]sort="))
    expect_same_document(page)


def test_the_tour_toggle_switches_to_all_time_in_place(page: Page, world: World) -> None:
    """TD-26: a profile opens on the current tour; the toggle (and the select) swap <main> to the all-time view."""
    page.goto(f"/players/{world.ace_pk}/")
    mark_page(page)
    toggle = page.get_by_role("group", name="Tour")
    expect(toggle.get_by_role("link", name="All time")).not_to_have_attribute("aria-current", "true")

    toggle.get_by_role("link", name="All time").click()

    expect(page).to_have_url(re.compile(r"[?&]tour=all"))
    expect(toggle.get_by_role("link", name="All time")).to_have_attribute("aria-current", "true")
    expect_same_document(page)
    expect_heading(page, "Recent sorties", level=2)
