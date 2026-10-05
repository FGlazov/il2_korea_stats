"""User flows around the aircraft pages (maintainer's list 2026-10-04, items 1 and 3):

1. a player reads his sortie, sees what shot him down, and goes to that aircraft to learn what it is weak against
   (the matchups: kills, losses, exchange rate K/L, and the intercept-only toggle);
3. the aircraft rankings (`/aircraft/`): tour selector, sorting by several columns, optional columns, and the filters
   and, on a type's page, the weapon-mods filter.

The arena missions of `tests/e2e/world.py` give the numbers: Ace (MiG-15bis) beats Rex Rival (F-86A-5) in 8 of 12
duels, and the featured mission adds one MiG kill of an F-86A-5 and one F-86A-5 kill of a MiG. Never asserted: times.
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
from tests.e2e.helpers import expect_heading, link_or_button, main_region
from tests.e2e.world import World

patient_timeouts = flow_helpers.patient_timeouts  # the long flows get generous timeouts
pytestmark = pytest.mark.usefixtures("patient_timeouts")

MIG = "MiG-15bis"
SABRE = "F-86A-5"


def matchups(page: Page) -> list[dict[str, str]]:
    return rows_of(table_with(page, "Enemy aircraft", "Kills", "Losses", "K/L"))


def aircraft_table(page: Page) -> list[dict[str, str]]:
    return rows_of(table_with(page, "Aircraft", "Sorties", "Pilots"))


def open_aircraft_rankings(page: Page) -> None:
    page.get_by_role("navigation", name="Main").get_by_role("link", name="Aircraft").click()
    expect(page).to_have_url(re.compile(r"/aircraft/$"))


# --- 1. what shot me down is weak against ---------------------------------------------------------------------------


def test_a_shot_down_player_learns_what_the_enemy_aircraft_is_weak_against(page: Page, world: World) -> None:
    """Sortie ("Shot down by" Rex Rival's F-86A-5) -> aircraft rankings -> the F-86A-5 -> matchups: it loses more
    often to MiG-15bis than it wins (the exchange rate K/L is under 1) -> intercept-only fights -> the MiG's own page,
    which shows the mirror image."""
    page.goto(f"/sorties/{world.arena_loss_sortie_pk}/")
    expect_heading(page, world.ace, level=1)
    expect_heading(page, "Shot down by", level=2)
    attackers = main_region(page).get_by_role("table").filter(has=page.get_by_role("columnheader", name="Attacker"))
    expect(attackers.get_by_role("link", name=world.rival)).to_be_visible()
    expect(attackers.get_by_role("link", name=SABRE)).to_be_visible()

    # the rankings list the attacker's aircraft; open it
    open_aircraft_rankings(page)
    ranking = aircraft_table(page)
    assert SABRE in names_in(ranking, "Aircraft")
    assert MIG in names_in(ranking, "Aircraft")
    table_with(page, "Aircraft", "Sorties").get_by_role("link", name=SABRE, exact=True).click()
    expect(page).to_have_url(re.compile(r"/aircraft/\d+/(\?.*)?$"))
    expect_heading(page, SABRE, level=1)

    # what it is weak against: the MiG-15bis (it won 5 of their 14 fights: Rex 4 times, Charlie once)
    expect_heading(page, "Matchups", level=2)
    row = row_for(matchups(page), MIG, "Enemy aircraft")
    assert (number(row["Kills"]), number(row["Losses"]), number(row["Kills + losses"])) == (5, 9, 14)
    assert number(row["K/L"]) == pytest.approx(5 / 9, abs=0.01)
    assert number(row["K/L"]) < 1

    # the intercept-only view counts the duels between air superiority sorties: the featured mission drops out
    link_or_button(page, "Intercept sorties only").click()
    expect(page).to_have_url(re.compile(r"[?&]intercept=1"))
    row = row_for(matchups(page), MIG, "Enemy aircraft")
    assert (number(row["Kills"]), number(row["Losses"])) == (4, 8)
    assert number(row["K/L"]) == pytest.approx(0.5, abs=0.01)
    link_or_button(page, "All kills and losses").click()
    expect(page).not_to_have_url(re.compile(r"intercept=1"))
    assert number(row_for(matchups(page), MIG, "Enemy aircraft")["Kills"]) == 5

    # the enemy's name leads to its page, where the same fights read the other way round
    table_with(page, "Enemy aircraft", "Kills").get_by_role("link", name=MIG, exact=True).click()
    expect_heading(page, MIG, level=1)
    row = row_for(matchups(page), SABRE, "Enemy aircraft")
    assert (number(row["Kills"]), number(row["Losses"])) == (9, 5)
    assert number(row["K/L"]) == pytest.approx(9 / 5, abs=0.01)


def test_the_top_pilots_of_an_aircraft_lead_to_their_profiles(page: Page, world: World) -> None:
    """The MiG-15bis page ranks pilots by Elo; Ace is the only one with enough encounters, his name opens his page."""
    page.goto("/aircraft/")
    table_with(page, "Aircraft", "Sorties").get_by_role("link", name=MIG, exact=True).click()
    expect_heading(page, "Top pilots by Elo", level=2)
    ranking = main_region(page).get_by_role("table").filter(has=page.get_by_role("columnheader", name="Elo")).first
    ranking.get_by_role("link", name=world.ace).click()
    expect(page).to_have_url(re.compile(rf"/players/{world.ace_pk}/"))


# --- 3. the rankings with their filters -----------------------------------------------------------------------------


def sorties_of(page: Page, aircraft: str) -> float:
    return number(row_for(aircraft_table(page), aircraft, "Aircraft")["Sorties"])


def test_the_tour_selector_splits_the_season_and_all_time_adds_it_up(page: Page, world: World) -> None:
    """Every tour shows its own sorties per type; the tours add up to "All time" (a `?tour=all` link keeps working)."""
    page.goto("/aircraft/?tour=all")
    expect(tour_select(page)).to_have_value("all")
    all_time = sorties_of(page, MIG)
    assert all_time >= 12

    per_tour: list[float] = []
    options = tour_select(page).locator("option")
    tours = [
        (options.nth(i).get_attribute("value") or "", options.nth(i).inner_text().strip())
        for i in range(options.count())
        if re.search(r"\d{4}", options.nth(i).inner_text())
    ]
    assert len(tours) >= 3, tours  # September, August, July 2026 (the fillers go back that far)
    for value, label in tours:
        tour_select(page).select_option(label=label)
        expect(page).to_have_url(re.compile(rf"[?&]tour={value}$"))
        settle(page)
        rows = names_in(aircraft_table(page), "Aircraft")
        per_tour.append(sorties_of(page, MIG) if MIG in rows else 0)
    assert sum(per_tour) == all_time, (per_tour, all_time)

    tour_select(page).select_option(label="All time")
    expect(page).to_have_url(re.compile(r"[?&]tour=all"))
    settle(page)
    assert sorties_of(page, MIG) == all_time
    tour_select(page).select_option(label="Current tour")
    expect(page).not_to_have_url(re.compile(r"tour=all"))
    settle(page)
    assert sorties_of(page, MIG) <= all_time


def test_sorting_the_rankings_by_several_columns_in_turn(page: Page, world: World) -> None:
    """Pilots, Air kills, then the first column again (A-Z): the rows follow each time, the header says which way, the
    address keeps the choice (and the tour)."""
    page.goto("/aircraft/?tour=all")

    for header in ("Pilots", "Air kills", "Deaths"):
        column_header(page, header).get_by_role("link").click()
        expect(column_header(page, header)).to_have_attribute("aria-sort", "descending")
        expect(page).to_have_url(re.compile(r"[?&]sort=-"))
        assert "tour=all" in page.url
        values = [number(row[header]) for row in aircraft_table(page)]
        assert values == sorted(values, reverse=True), (header, values)

    column_header(page, "Aircraft").get_by_role("link").click()
    expect(column_header(page, "Aircraft")).to_have_attribute("aria-sort", "ascending")
    names = names_in(aircraft_table(page), "Aircraft")
    assert names == sorted(names, key=str.lower)


def test_optional_columns_join_the_table_and_can_be_sorted_on(page: Page) -> None:
    """Extra columns: Bailouts and Assists appear (in place), the address lists both, Bailouts sorts, and a reload
    keeps all of it."""
    page.goto("/aircraft/?tour=all")
    page.get_by_text("Extra columns", exact=True).click()
    for label in ("Bailouts", "Assists"):
        page.get_by_role("checkbox", name=label, exact=True).check()
        expect(column_header(page, label)).to_be_visible()
    assert page.url.count("cols=") == 2, page.url

    column_header(page, "Bailouts").get_by_role("link").click()
    expect(column_header(page, "Bailouts")).to_have_attribute("aria-sort", re.compile("ascending|descending"))
    assert page.url.count("cols=") == 2, page.url
    assert "sort=" in page.url, page.url
    values = [number(row["Bailouts"]) for row in aircraft_table(page)]
    assert values == sorted(values, reverse=True) or values == sorted(values)
    assert len(set(values)) > 1  # the sort is not trivially satisfied: the arena has bail-outs (Prop Pete and Paula)

    url = page.url
    page.reload()
    assert page.url == url
    for label in ("Bailouts", "Assists"):
        expect(column_header(page, label)).to_be_visible()


def test_the_role_toggle_splits_air_superiority_from_attack(page: Page) -> None:
    """Role toggle (all / air superiority / attack): attack shows the aircraft that flew attack sorties (the F-51D of
    Gunther Groundpounder), not the MiG-15bis; air superiority shows the duelling types."""
    page.goto("/aircraft/?tour=all")
    toggle = main_region(page).get_by_role("group", name="Which sorties to count")
    air = toggle.get_by_role("link", name="Air superiority", exact=True)
    attack = toggle.get_by_role("link", name="Attack", exact=True)
    everything = toggle.get_by_role("link", name="All roles", exact=True)

    attack.click()
    expect(attack).to_have_attribute("aria-current", "true")
    names = names_in(aircraft_table(page), "Aircraft")
    assert "F-51D" in names
    assert MIG not in names
    air.click()
    expect(air).to_have_attribute("aria-current", "true")
    names = names_in(aircraft_table(page), "Aircraft")
    assert {MIG, SABRE} <= set(names)
    everything.click()
    expect(everything).to_have_attribute("aria-current", "true")
    assert {MIG, SABRE, "F-51D"} <= set(names_in(aircraft_table(page), "Aircraft"))


def test_the_weapon_mods_filter_follows_into_every_section_of_the_aircraft_page(page: Page) -> None:
    """Weapon mods filter (aircraft page, OQ-122): per significant modification a group "Filter by modification <name>"
    with the links Any / With / Without. Choosing one puts it in the address, keeps the page whole and the choice
    marked; Any brings the plain page back. The tour stays (all time here)."""
    page.goto("/aircraft/?tour=all")
    main_region(page).get_by_role("link", name=MIG, exact=True).first.click()
    expect_heading(page, MIG, level=1)
    groups = main_region(page).get_by_role("group", name=re.compile(r"^Filter by modification "))
    assert groups.count() >= 1
    group = groups.first
    for label in ("Any", "With", "Without"):
        expect(group.get_by_role("link", name=label, exact=True)).to_be_visible()
    expect(group.get_by_role("link", name="Any", exact=True)).to_have_attribute("aria-current", "true")

    group.get_by_role("link", name="With", exact=True).click()
    expect(page).to_have_url(re.compile(r"[?&]mod\d+=with"))
    expect_heading(page, MIG, level=1)
    expect(group.get_by_role("link", name="With", exact=True)).to_have_attribute("aria-current", "true")
    for section in ("Matchups", "Loadouts", "Modifications"):
        expect_heading(page, section, level=2)

    group.get_by_role("link", name="Any", exact=True).click()
    expect(page).not_to_have_url(re.compile(r"[?&]mod\d+="))
    expect(group.get_by_role("link", name="Any", exact=True)).to_have_attribute("aria-current", "true")
