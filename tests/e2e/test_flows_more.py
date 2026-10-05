"""More user flows (maintainer's list 2026-10-04, items 4 to 6): achievements and where they were earned, several
optional columns used together, sharing a sortie (Open Graph tags, deep links), an admin hiding a player, an older tour,
a rivalry, streaks, a phone, no JavaScript, keyboard only.

Same conventions as `test_flows.py`: click by role and name, find tables by headers, assert on data (names, counts,
URLs), never on times or flavor text. Data: `tests/e2e/world.py` (the arena missions: Ace and Rex Rival duel, Rex wins
every third, so Ace dies in some of them: his streaks end).
"""

import re

import pytest
from playwright.sync_api import Browser, Page, expect

from tests.e2e import flow_helpers
from tests.e2e.flow_helpers import (
    admin_login,
    column_header,
    names_in,
    no_horizontal_scroll,
    number,
    row_for,
    rows_of,
    settle,
    tab_to,
    table_with,
    tour_select,
)
from tests.e2e.helpers import expect_heading, header_search, link_or_button, main_region, sortie_links
from tests.e2e.world import World

patient_timeouts = flow_helpers.patient_timeouts  # the long flows get generous timeouts
pytestmark = pytest.mark.usefixtures("patient_timeouts")

PHONE = {"width": 390, "height": 844}


# --- 4. achievements --------------------------------------------------------------------------------------------------


def test_a_player_follows_an_achievement_to_the_sortie_it_was_earned_in(page: Page, world: World) -> None:
    """Profile -> his achievements -> one earned tier links to its sortie -> that sortie says "Earned in this
    sortie" and leads back to the pilot's achievements."""
    page.goto(f"/players/{world.ace_pk}/")
    expect_heading(page, "Achievements", level=2)
    link_or_button(page, re.compile("All achievements")).first.click()
    expect(page).to_have_url(re.compile(rf"/players/{world.ace_pk}/achievements/"))
    expect(page.get_by_role("heading", level=1)).to_contain_text(world.ace)

    earned = main_region(page).locator("a[href^='/sorties/']")
    assert earned.count() >= 3  # Ace holds several tiers, each linking to the sortie that earned it
    target = earned.first.get_attribute("href")
    assert target is not None
    earned.first.click()
    expect(page).to_have_url(re.compile(re.escape(target) + "$"))
    expect_heading(page, world.ace, level=1)
    expect(main_region(page).get_by_text("Earned in this sortie")).to_be_visible()

    link_or_button(page, re.compile("All achievements of this pilot")).first.click()
    expect(page).to_have_url(re.compile(rf"/players/{world.ace_pk}/achievements/"))


def test_the_achievement_overview_leads_to_the_holders_of_a_tier(page: Page, world: World) -> None:
    """Overview of all achievements -> the holders of one tier (a list of pilots) -> a holder's profile."""
    page.goto("/achievements/")
    expect_heading(page, "Achievements", level=1)
    holders = main_region(page).locator("a[href*='/achievements/'][href*='tier=']").filter(has_text=re.compile("pilot"))
    assert holders.count() >= 3
    holders.first.click()
    expect(page).to_have_url(re.compile(r"/achievements/[\w-]+/\?(.*&)?tier=\d"))  # the tour may come first
    pilots = main_region(page).locator("a[href^='/players/']")
    assert pilots.count() >= 1
    name = pilots.first.inner_text()
    pilots.first.click()
    expect(page).to_have_url(re.compile(r"/players/\d+/"))
    expect_heading(page, name, level=1)


# --- 5. several optional columns, sorted, paged -----------------------------------------------------------------------


def test_two_optional_columns_sorted_in_turn_and_paged_keep_the_view_through_a_reload(page: Page, world: World) -> None:
    """Players: add K/D and Air score, sort by one then the other, go to page 2; the address keeps both columns, the
    sort and the page, and a reload restores the view."""
    page.goto("/players/?q=")
    page.get_by_text("Columns", exact=True).click()
    for label in ("K/D", "Air score"):
        page.get_by_role("checkbox", name=label, exact=True).check()
        expect(column_header(page, label)).to_be_visible()
    assert page.url.count("cols=") == 2, page.url

    def finite(header: str) -> list[float]:
        return [
            value for value in (number(row[header]) for row in rows_of(table_with(page, "Player"))) if value == value
        ]

    for header in ("K/D", "Air score"):
        column_header(page, header).get_by_role("link").click()
        expect(column_header(page, header)).to_have_attribute("aria-sort", re.compile("ascending|descending"))
        settle(page)
        assert page.url.count("cols=") == 2, page.url
        assert "sort=" in page.url, page.url
        values = finite(header)
        assert len(values) >= 2
        descending = column_header(page, header).get_attribute("aria-sort") == "descending"
        assert values == sorted(values, reverse=descending), (header, values)

    first_page = names_in(rows_of(table_with(page, "Player")), "Player")
    page.get_by_role("link", name="Next").click()
    expect(page).to_have_url(re.compile(r"[?&]page=2"))
    settle(page)
    second_page = names_in(rows_of(table_with(page, "Player")), "Player")
    assert second_page
    assert not set(second_page) & set(first_page)
    for label in ("K/D", "Air score"):
        expect(column_header(page, label)).to_be_visible()
    assert page.url.count("cols=") == 2, page.url
    assert "sort=" in page.url, page.url

    url = page.url
    page.reload()
    assert page.url == url
    for label in ("K/D", "Air score"):
        expect(column_header(page, label)).to_be_visible()
    assert names_in(rows_of(table_with(page, "Player")), "Player") == second_page
    expect(column_header(page, "Air score")).to_have_attribute("aria-sort", re.compile("ascending|descending"))


# --- 6a. sharing ------------------------------------------------------------------------------------------------------


def meta(page: Page, key: str) -> str:
    content = page.locator(f"meta[property='{key}'], meta[name='{key}']").first.get_attribute("content")
    assert content is not None, f"no <meta> {key}"
    return content


def test_a_shared_sortie_carries_open_graph_tags_that_match_the_page(page: Page, world: World) -> None:
    """A link pasted into a chat shows the pilot, the aircraft and the outcome (og:title), the sortie's own address
    (og:url) and its numbers (og:description)."""
    page.goto(f"/sorties/{world.arena_loss_sortie_pk}/")
    title = meta(page, "og:title")
    assert world.ace in title
    assert "MiG-15bis" in title
    assert meta(page, "twitter:title") == title
    assert meta(page, "og:url").endswith(f"/sorties/{world.arena_loss_sortie_pk}/")
    assert meta(page, "og:site_name")
    assert meta(page, "twitter:card")
    assert re.search(r"\b1 air kills\b", meta(page, "og:description"))  # the sortie's own figure
    assert title in page.title()


def test_a_deep_link_restores_tour_sort_and_columns_for_someone_else(
    page: Page, browser: Browser, base_url: str, world: World
) -> None:
    """Set up a view (all time, sorted by Pilots, three extra columns), copy the address, open it in a fresh browser
    context (nobody's cookies, no stored choices): the same view comes up."""
    page.goto("/aircraft/")
    tour_select(page).select_option(label="All time")
    expect(page).to_have_url(re.compile(r"tour=all"))
    settle(page)
    page.get_by_text("Columns", exact=True).click()
    for label in ("Bailouts", "Assists", "Pilots"):
        page.get_by_role("checkbox", name=label, exact=True).check()
        expect(column_header(page, label)).to_be_visible()
    column_header(page, "Pilots").get_by_role("link").click()
    expect(column_header(page, "Pilots")).to_have_attribute("aria-sort", "descending")
    settle(page)
    shared = page.url
    mine = rows_of(table_with(page, "Aircraft", "Pilots"))

    other = browser.new_context(base_url=base_url)
    try:
        theirs_page = other.new_page()
        theirs_page.goto(shared)
        expect(tour_select(theirs_page)).to_have_value("all")
        for label in ("Bailouts", "Assists", "Pilots"):
            expect(column_header(theirs_page, label)).to_be_visible()
        expect(column_header(theirs_page, "Pilots")).to_have_attribute("aria-sort", "descending")
        assert rows_of(table_with(theirs_page, "Aircraft", "Pilots")) == mine
    finally:
        other.close()


# --- 6b. an admin hides a player --------------------------------------------------------------------------------------


def test_hiding_a_player_in_the_admin_removes_him_from_the_public_pages(
    page: Page, browser: Browser, base_url: str, world: World
) -> None:
    """FR-ADM-3: tick "Is hidden" on a player in the admin; for a visitor he is gone from the search, the boards and
    his own pages (404), and his rows in a mission read "Hidden player". Unhiding restores everything."""
    visitor = browser.new_context(base_url=base_url)
    seen = visitor.new_page()

    def status(path: str) -> int:
        return visitor.request.get(base_url + path).status

    def set_hidden(hidden: bool) -> None:
        page.goto(f"/admin/il2ks_db/player/{world.hank_pk}/change/")
        box = page.get_by_label("Is hidden")
        box.check() if hidden else box.uncheck()
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page).to_have_url(re.compile(r"/admin/il2ks_db/player/$"))

    try:
        # before: he is a normal pilot
        assert status(f"/players/{world.hank_pk}/") == 200
        seen.goto(f"/players/?q={world.hank.split()[0]}")
        expect(main_region(seen).get_by_role("link", name=world.hank)).to_be_visible()

        admin_login(page)
        set_hidden(True)

        assert status(f"/players/{world.hank_pk}/") == 404
        assert status(f"/players/{world.hank_pk}/killboard/") == 404
        assert status(f"/players/{world.hank_pk}/sorties/") == 404
        seen.goto(f"/players/?q={world.hank.split()[0]}")
        expect(main_region(seen).get_by_role("link", name=world.hank)).to_have_count(0)
        seen.goto("/leaderboards/air/?tour=all")
        assert world.hank not in names_in(rows_of(table_with(seen, "Player")), "Player")
        seen.goto(f"/missions/{world.arena_mission_pk}/")
        expect(main_region(seen).get_by_text("Hidden player").first).to_be_visible()
        expect(main_region(seen).get_by_text(world.hank)).to_have_count(0)
        expect(main_region(seen).get_by_role("link", name=world.rival).first).to_be_visible()  # the others stay
    finally:
        set_hidden(False)
        visitor_status = status(f"/players/{world.hank_pk}/")
        visitor.close()
    assert visitor_status == 200


# --- 6c. an older tour ------------------------------------------------------------------------------------------------


def test_someone_browses_an_older_tour_and_comes_back_to_the_current_one(page: Page, world: World) -> None:
    """Missions: pick August -> only August's missions (not the arena's, not the story's) -> open one -> back -> the
    leaderboards of August (Ace, no Rex Rival) -> back on the current tour everything is there again."""
    page.goto("/missions/")
    current = main_region(page).locator("a[href^='/missions/']")
    current_hrefs = {link.get_attribute("href") for link in current.all()}
    assert f"/missions/{world.featured_mission_pk}/" in current_hrefs

    tour_select(page).select_option(label="August 2026")
    expect(page).to_have_url(re.compile(r"[?&]tour=\d+"))
    settle(page)
    august = {link.get_attribute("href") for link in main_region(page).locator("a[href^='/missions/']").all()}
    assert august
    assert f"/missions/{world.featured_mission_pk}/" not in august
    assert f"/missions/{world.arena_mission_pk}/" not in august

    main_region(page).get_by_role("row").nth(1).get_by_role("link").first.click()
    expect(page).to_have_url(re.compile(r"/missions/\d+/$"))
    expect_heading(page, re.compile("."), level=1)
    page.go_back()
    expect(page).to_have_url(re.compile(r"/missions/\?.*tour=\d+"))

    page.goto("/leaderboards/air/")
    tour_select(page).select_option(label="August 2026")
    settle(page)
    names = names_in(rows_of(table_with(page, "Player")), "Player")
    assert world.ace in names
    assert world.rival not in names
    tour_select(page).select_option(label="Current tour")
    expect(page).not_to_have_url(re.compile(r"tour=\d"))
    settle(page)
    names = names_in(rows_of(table_with(page, "Player")), "Player")
    assert world.rival in names


# --- 6d. rivalry ------------------------------------------------------------------------------------------------------


def test_a_rivalry_from_the_sortie_to_the_opponent_the_killboard_and_the_encounter(page: Page, world: World) -> None:
    """Ace's lost duel -> the attacker's profile -> his killboard (Ace: 4 kills for Rex, 8 deaths) -> the last encounter
    (a mission) -> Rex's sortie there."""
    page.goto(f"/sorties/{world.arena_loss_sortie_pk}/")
    attackers = main_region(page).get_by_role("table").filter(has=page.get_by_role("columnheader", name="Attacker"))
    attackers.get_by_role("link", name=world.rival, exact=True).click()
    expect(page).to_have_url(re.compile(rf"/players/{world.rival_pk}/"))
    expect_heading(page, world.rival, level=1)

    link_or_button(page, re.compile("Full killboard")).first.click()
    expect(page).to_have_url(re.compile(rf"/players/{world.rival_pk}/killboard/"))
    by_player = table_with(page, "Opponent", "Kills", "Deaths")
    row = row_for(rows_of(by_player), world.ace, "Opponent")
    assert (number(row["Kills"]), number(row["Deaths"])) == (4, 8)  # Rex: 4 wins and 8 losses against Ace

    by_player.get_by_role("row").filter(has=page.get_by_role("link", name=world.ace, exact=True)).locator(
        "a[href^='/missions/']"
    ).click()
    expect(page).to_have_url(re.compile(r"/missions/\d+/$"))
    detail = main_region(page)
    expect(detail.get_by_role("link", name=world.ace).first).to_be_visible()
    expect(detail.get_by_role("link", name=world.rival).first).to_be_visible()
    detail.get_by_role("row").filter(has=page.get_by_role("link", name=world.rival, exact=True)).first.get_by_role(
        "link", name="Sortie details"
    ).click()
    expect(page).to_have_url(re.compile(r"/sorties/\d+/$"))
    expect_heading(page, world.rival, level=1)
    expect(main_region(page).get_by_text("F-86A-5").first).to_be_visible()


# --- 6e. streaks ------------------------------------------------------------------------------------------------------


def test_streaks_from_a_sortie_to_the_history_and_the_sortie_that_ended_one(page: Page, world: World) -> None:
    """Sortie -> the pilot -> best streaks -> all streaks (runs of 2+ sorties without a death) -> the sortie in which a
    run ended ("Died")."""
    page.goto(f"/sorties/{world.arena_loss_sortie_pk}/")
    main_region(page).get_by_role("link", name=world.ace, exact=True).first.click()
    expect(page).to_have_url(re.compile(rf"/players/{world.ace_pk}/"))
    link_or_button(page, re.compile("Best streaks")).first.click()
    expect(page).to_have_url(re.compile(rf"/players/{world.ace_pk}/streaks/"))
    link_or_button(page, re.compile("All streaks")).first.click()
    expect(page).to_have_url(re.compile(rf"/players/{world.ace_pk}/streaks/history/"))

    runs = rows_of(table_with(page, "Since", "Until", "Sorties", "How it ended"))
    assert len(runs) >= 3
    ended = [row["How it ended"] for row in runs]
    assert sum("Died" in cell for cell in ended) >= 2  # Rex killed Ace in some of the duels
    assert sum("Still going" in cell for cell in ended) == 1
    assert all(number(row["Sorties"]) >= 2 for row in runs)

    died = main_region(page).get_by_role("link", name=re.compile("Died"))
    died.first.click()
    expect(page).to_have_url(re.compile(r"/sorties/\d+/$"))
    expect_heading(page, world.ace, level=1)
    expect_heading(page, "Shot down by", level=2)  # the sortie that ended the run


# --- 6f. a phone ------------------------------------------------------------------------------------------------------


def test_the_site_is_usable_on_a_phone(browser: Browser, base_url: str, world: World) -> None:
    """390x844: search a pilot, open his profile and a sortie; nothing makes the page scroll sideways."""
    context = browser.new_context(base_url=base_url, viewport=PHONE)  # type: ignore[arg-type]
    page = context.new_page()
    page.set_default_timeout(20_000)
    try:
        page.goto("/")
        assert no_horizontal_scroll(page)
        header_search(page).fill("Rex")
        header_search(page).press("Enter")
        expect(page).to_have_url(re.compile(r"/players/\?q=Rex"))
        assert no_horizontal_scroll(page)
        main_region(page).get_by_role("link", name=world.rival).click()
        expect_heading(page, world.rival, level=1)
        assert no_horizontal_scroll(page)
        page.locator("#recent").locator("a[href^='/sorties/']").first.click()
        expect(page).to_have_url(re.compile(r"/sorties/\d+/$"))
        expect_heading(page, world.rival, level=1)
        assert no_horizontal_scroll(page)
        for path in ("/leaderboards/air/", "/aircraft/", "/missions/"):
            page.goto(path)
            assert no_horizontal_scroll(page), path
            expect(page.get_by_role("heading", level=1)).to_be_visible()
    finally:
        context.close()


def test_language_and_dark_mode_persist_across_pages(browser: Browser, base_url: str, world: World) -> None:
    """Dark mode and the language are remembered while browsing: profile, sortie, board."""
    context = browser.new_context(base_url=base_url, viewport=PHONE)  # type: ignore[arg-type]
    page = context.new_page()
    page.set_default_timeout(20_000)
    html = page.locator("html")
    try:
        page.emulate_media(color_scheme="light")
        page.goto("/")
        page.get_by_role("button", name="Switch between light and dark").click()
        expect(html).to_have_attribute("data-theme", "dark")
        menu = page.locator("details.language-menu")
        menu.locator("summary").click()
        menu.get_by_role("link", name="Deutsch").click()
        expect(html).to_have_attribute("lang", "de")
        for path in (f"/players/{world.ace_pk}/", f"/sorties/{world.ace_sortie_pk}/", "/leaderboards/air/"):
            page.goto(path)
            expect(html).to_have_attribute("lang", "de")
            expect(html).to_have_attribute("data-theme", "dark")
        page.goto("/")
        expect(page.locator("details.language-menu summary")).to_contain_text("Deutsch")
    finally:
        context.close()


# --- 6g. without JavaScript -------------------------------------------------------------------------------------------


def test_sorting_filtering_and_paging_work_without_javascript(browser: Browser, base_url: str, world: World) -> None:
    """Plain links and forms: a sortable header, the Apply button of a filter, the pagination links."""
    context = browser.new_context(base_url=base_url, java_script_enabled=False)
    page = context.new_page()
    page.set_default_timeout(20_000)
    try:
        page.goto("/players/?q=")
        column_header(page, "Sorties").get_by_role("link").click()
        expect(page).to_have_url(re.compile(r"[?&]sort="))
        values = [number(row["Sorties"]) for row in rows_of(table_with(page, "Player", "Sorties"))]
        assert values == sorted(values, reverse=True) or values == sorted(values)
        page.get_by_role("link", name="Next").click()
        expect(page).to_have_url(re.compile(r"[?&]page=2"))
        assert "sort=" in page.url  # the page link keeps the sort
        page.get_by_role("link", name="Previous").click()
        expect(page).to_have_url(re.compile(r"/players/\?"))

        page.goto("/leaderboards/air/?tour=all")
        main_region(page).get_by_role("combobox", name=re.compile("^Propulsion")).select_option(label="Propeller")
        main_region(page).get_by_role("button", name="Apply").click()
        expect(page).to_have_url(re.compile(r"[?&]pool=prop"))
        names = names_in(rows_of(table_with(page, "Player")), "Player")
        assert world.pete in names
        assert world.ace not in names
        page.get_by_role("navigation", name="Leaderboards").get_by_role("link", name="Elo (jet)").click()
        expect(page).to_have_url(re.compile(r"/leaderboards/elo-jet/"))
        assert names_in(rows_of(table_with(page, "Player")), "Player")[0] == world.ace
    finally:
        context.close()


# --- 6h. keyboard only ------------------------------------------------------------------------------------------------


def test_a_table_row_link_opens_with_the_keyboard(page: Page, world: World) -> None:
    """Tab to the first link of the mission table and press Enter; the same on the player list."""
    page.goto("/missions/")
    tab_to(page, "main tbody a")
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(r"/missions/\d+/$"))
    expect(page.get_by_role("heading", level=1)).to_be_visible()

    page.goto("/players/?q=")
    tab_to(page, "main tbody a[href^='/players/']")
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(r"/players/\d+/(\?.*)?$"))
    expect(page.get_by_role("heading", level=1)).to_be_visible()
    assert sortie_links(page).count() >= 1
