"""User flows on the leaderboards (maintainer's list 2026-10-04, item 2): someone scans the boards, switches among them
(Elo jet and prop, air score, interception, ground per hour, tank busting, ground score, play time), narrows them by
tour, propulsion and aircraft, finds himself and his rank, and opens a rival's profile to compare figures.

Data: the arena missions of `tests/e2e/world.py`. Ace flew a MiG-15bis in all of them and won 8 of 12 duels against Rex
Rival (F-86A-5); Prop Pete and Prop Paula duel in F-51Ds; Gunther Groundpounder flies attack sorties; Ace shoots an
Il-10 down every mission (interception). Asserted: names, ranks, orderings and figures, never times.
"""

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e import flow_helpers
from tests.e2e.flow_helpers import (
    column_header,
    names_in,
    number,
    row_for,
    rows_of,
    settle,
    table_with,
    tour_select,
)
from tests.e2e.helpers import expect_heading, main_region
from tests.e2e.world import World

patient_timeouts = flow_helpers.patient_timeouts  # the long flows get generous timeouts
pytestmark = pytest.mark.usefixtures("patient_timeouts")


def board(page: Page) -> list[dict[str, str]]:
    """The leaderboard table of the page (the only table in the main region)."""
    return rows_of(table_with(page, "Player"))


def players(page: Page) -> list[str]:
    return names_in(board(page), "Player")


def open_board(page: Page, name: str, slug: str) -> None:
    """Click the board's button in the switcher (the "Leaderboards" navigation)."""
    page.get_by_role("navigation", name="Leaderboards").get_by_role("link", name=name, exact=True).click()
    expect(page).to_have_url(re.compile(rf"/leaderboards/{slug}/"))
    expect(
        page.get_by_role("navigation", name="Leaderboards").get_by_role("link", name=name, exact=True)
    ).to_have_attribute("aria-current", "page")


def test_a_player_scans_the_boards_and_finds_himself_on_each(page: Page, world: World) -> None:
    """Every board in turn, from the switcher: who is on it, in what order, with which figures."""
    page.goto("/leaderboards/")
    expect_heading(page, "Leaderboards", level=1)

    # Elo (jet): the two jet duellists, Ace ahead of Rex (he won 8 of 12), both with 12 encounters
    open_board(page, "Elo (jet)", "elo-jet")
    rows = board(page)
    assert names_in(rows, "Player")[:2] == [world.ace, world.rival]
    ace, rex = row_for(rows, world.ace, "Player"), row_for(rows, world.rival, "Player")
    assert number(ace["Elo"]) > number(rex["Elo"])
    assert number(ace["Encounters"]) == number(rex["Encounters"]) == 12
    assert world.pete not in players(page)  # propeller pilots are not on the jet board

    # Elo (prop): the F-51D pair, no jet pilots
    open_board(page, "Elo (prop)", "elo-prop")
    assert sorted(players(page)) == sorted([world.pete, world.paula])
    elo = [number(row["Elo"]) for row in board(page)]
    assert elo == sorted(elo, reverse=True)

    # Air score (opens on the current tour): Ace first by a distance, Rex fourth, a pilot without kills at 0
    open_board(page, "Air score", "air")
    rows = board(page)
    assert players(page)[0] == world.ace
    ace, rex = row_for(rows, world.ace, "Player"), row_for(rows, world.rival, "Player")
    assert number(ace["Air score"]) > number(rex["Air score"]) > 0
    assert number(ace["Air kills"]) > number(rex["Air kills"])
    assert number(rex["Deaths"]) > number(ace["Deaths"])  # Rex lost 8 duels, Ace 4

    # Interception: Ace is the only one who shoots bombers and attackers down (the AI Il-10 of every mission)
    open_board(page, "Interception", "interception")
    rows = board(page)
    assert players(page)[0] == world.ace
    shot_down = "Bombers, attackers and transports shot down"
    assert number(rows[0][shot_down]) == 12
    assert number(row_for(rows, world.rival, "Player")[shot_down]) == 0

    # Ground boards: only the attack pilot is on the per-hour and tank boards, and Ace shows on the plain ground board
    open_board(page, "Attack proficiency", "ground-hour")
    assert players(page) == [world.gunther]
    open_board(page, "Tank busting", "tank-busting")
    assert players(page) == [world.gunther]
    assert number(board(page)[0]["Tanks destroyed"]) == 36  # 3 tanks in each of 12 sorties
    open_board(page, "Ground score", "ground")
    assert players(page)[0] == world.gunther
    assert world.ace in players(page)
    assert number(row_for(board(page), world.ace, "Player")["Ground score"]) == 0

    # Flight time: Gunther (30 minutes a sortie) ahead of Ace
    open_board(page, "Flight time", "play-time")
    names = players(page)
    assert names.index(world.gunther) < names.index(world.ace) < names.index(world.rival)


def test_a_player_compares_himself_with_a_rival_across_boards(page: Page, world: World) -> None:
    """Air score board -> Rex Rival's profile (carrying the board's tour) -> his figures match the board's row."""
    page.goto("/leaderboards/air/")
    ace_row = row_for(board(page), world.ace, "Player")
    rex_row = row_for(board(page), world.rival, "Player")

    table_with(page, "Player").get_by_role("link", name=world.rival, exact=True).click()
    expect(page).to_have_url(re.compile(rf"/players/{world.rival_pk}/"))
    expect_heading(page, world.rival, level=1)
    text = main_region(page).inner_text()
    profile_score = re.search(r"Air score\s+(-?[\d.,]+)", text)
    assert profile_score is not None, text[:500]
    assert number(profile_score.group(1)) == number(rex_row["Air score"])
    assert number(rex_row["Air score"]) < number(ace_row["Air score"])

    # back on the board, Ace's own profile says the same about himself
    page.go_back()
    table_with(page, "Player").get_by_role("link", name=world.ace, exact=True).click()
    expect_heading(page, world.ace, level=1)
    ace_score = re.search(r"Air score\s+(-?[\d.,]+)", main_region(page).inner_text())
    assert ace_score is not None
    assert number(ace_score.group(1)) == number(ace_row["Air score"])

    # the Elo boards follow the tour like the others (a new tour is a clean slate): all time links the all-time profile
    page.goto("/leaderboards/elo-jet/?tour=all")
    table_with(page, "Player").get_by_role("link", name=world.rival, exact=True).click()
    expect(page).to_have_url(re.compile(rf"/players/{world.rival_pk}/\?tour=all"))


def test_the_tour_propulsion_and_aircraft_filters_narrow_a_board(page: Page, world: World) -> None:
    """Air score: another tour has other pilots, "Jet" drops the propeller pilots, "Propeller" the jet ones, and an
    aircraft type overrides the propulsion filter. The board's switcher keeps the filters."""
    page.goto("/leaderboards/air/?tour=all")
    expect(tour_select(page)).to_have_value("all")
    everyone = players(page)
    assert {world.ace, world.rival, world.pete, world.paula, world.gunther} <= set(everyone)

    # August (the fillers' and Ace's earlier sorties): Rex Rival has not flown yet
    august = tour_select(page).locator("option").filter(has_text=re.compile("^August")).get_attribute("value")
    assert august is not None
    tour_select(page).select_option(value=august)
    expect(page).to_have_url(re.compile(rf"[?&]tour={august}"))
    settle(page)
    assert world.ace in players(page)
    assert world.rival not in players(page)
    tour_select(page).select_option(label="All time")
    expect(page).to_have_url(re.compile(r"[?&]tour=all"))
    settle(page)

    pool = main_region(page).get_by_role("combobox", name=re.compile("^Propulsion"))
    pool.select_option(label="Jet")
    expect(page).to_have_url(re.compile(r"[?&]pool=jet"))
    settle(page)
    names = players(page)
    assert world.ace in names
    assert world.rival in names
    assert not {world.pete, world.paula, world.gunther} & set(names)

    pool.select_option(label="Propeller")
    expect(page).to_have_url(re.compile(r"[?&]pool=prop"))
    settle(page)
    names = players(page)
    assert {world.pete, world.paula, world.gunther} <= set(names)
    assert world.ace not in names

    # a chosen aircraft overrides the propulsion: the F-86A-5 pilots, although "Propeller" is still ticked
    aircraft = main_region(page).get_by_role("combobox", name=re.compile("^Aircraft"))
    aircraft.select_option(label="F-86A-5")
    expect(page).to_have_url(re.compile(r"[?&]aircraft=\d+"))
    settle(page)
    names = players(page)
    assert world.rival in names
    assert not {world.ace, world.pete} & set(names)

    # the switcher carries the tour to the next board
    open_board(page, "Interception", "interception")
    assert "tour=all" in page.url


def test_the_elo_boards_follow_the_tour(page: Page, world: World) -> None:
    """A new tour is a clean slate (maintainer, 2026-10-05): the Elo boards have the tour selector; all time shows the
    best tour's Elo."""
    page.goto("/leaderboards/elo-jet/?tour=all")
    expect(tour_select(page)).to_have_value("all")
    assert players(page)[0] == world.ace


def test_a_board_sorts_by_another_column_and_the_address_keeps_it(page: Page, world: World) -> None:
    """Air score board, all time: sort by Deaths (most first), then by Sorties; the order follows, the tour stays."""
    page.goto("/leaderboards/air/?tour=all")
    for header in ("Deaths", "Sorties"):
        column_header(page, header).get_by_role("link").click()
        expect(column_header(page, header)).to_have_attribute("aria-sort", "descending")
        settle(page)
        values = [number(row[header]) for row in board(page)]
        assert values == sorted(values, reverse=True), (header, values)
        assert "tour=all" in page.url, page.url
        assert "sort=-" in page.url, page.url
    page.reload()
    expect(column_header(page, "Sorties")).to_have_attribute("aria-sort", "descending")
