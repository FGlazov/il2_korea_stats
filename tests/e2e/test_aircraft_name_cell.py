"""The Jet/Prop tag on the aircraft list sits level with the name, clear of it (maintainer 2026-11-02)."""

from playwright.sync_api import Page

from tests.e2e.world import World

MEASURE = """() => {
  const cell = document.querySelector('.aircraft-table__name');
  const name = cell.querySelector('a').getBoundingClientRect();
  const tag = cell.querySelector('.badge').getBoundingClientRect();
  return {
    gap: tag.left - name.right,
    offset: Math.abs((tag.top + tag.bottom) / 2 - (name.top + name.bottom) / 2),
  };
}"""


def test_the_tag_is_level_with_the_aircraft_name_and_clear_of_it(page: Page, world: World) -> None:
    assert world.mission_count > 0
    page.goto("/aircraft/?tour=all")

    measured = page.evaluate(MEASURE)

    assert measured["gap"] >= 8
    assert measured["offset"] <= 1
