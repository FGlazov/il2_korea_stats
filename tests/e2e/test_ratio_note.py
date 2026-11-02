"""The note under the profile's ratios keeps clear of their box (maintainer 2026-11-02: it sat on the box's edge)."""

from playwright.sync_api import Page

from tests.e2e.world import World

# The note needs marks and a pilot below their minimum sorties, which the world has not got: place it as
# `players/detail_ratios.html` does, right after the box.
GAP_BELOW_THE_BOX = """() => {
  const box = document.querySelector('.kv--compact');
  const note = document.createElement('p');
  note.className = 'stat-mark-note';
  note.textContent = 'Marks start at 10 sorties.';
  box.after(note);
  return note.getBoundingClientRect().top - box.getBoundingClientRect().bottom;
}"""


def test_the_ratios_note_keeps_a_gap_to_the_box_above_it(page: Page, world: World) -> None:
    page.goto(f"/players/{world.ace_pk}/")
    assert page.evaluate(GAP_BELOW_THE_BOX) >= 6
