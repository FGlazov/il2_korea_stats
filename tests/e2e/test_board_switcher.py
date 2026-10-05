"""The leaderboard switcher (maintainer feedback 2026-10-04): every group label is fully visible and overlaps nothing,
the groups sit next to each other and wrap, no horizontal scrollbar, at phone, tablet and desktop widths."""

import pytest
from playwright.sync_api import Page

WIDTHS = (360, 768, 1280, 1920)

MEASURE = """() => {
  const rect = el => { const r = el.getBoundingClientRect(); return {l: r.left, r: r.right, t: r.top, b: r.bottom}; };
  const overlap = (a, b) => a.l < b.r - 0.5 && b.l < a.r - 0.5 && a.t < b.b - 0.5 && b.t < a.b - 0.5;
  const groups = [...document.querySelectorAll('.board-tabs__group')];
  const problems = [];
  const boxes = [];
  for (const group of groups) {
    const label = group.querySelector('.board-tabs__label');
    if (label.scrollWidth > label.clientWidth + 1) problems.push('label clipped: ' + label.textContent);
    boxes.push(['label ' + label.textContent, rect(label)]);
    for (const a of group.querySelectorAll('a')) boxes.push(['button ' + a.textContent.trim(), rect(a)]);
  }
  for (let i = 0; i < boxes.length; i++)
    for (let j = i + 1; j < boxes.length; j++)
      if (overlap(boxes[i][1], boxes[j][1])) problems.push(boxes[i][0] + ' overlaps ' + boxes[j][0]);
  const nav = rect(document.querySelector('.board-tabs'));
  for (const [name, r] of boxes) if (r.r > nav.r + 1 || r.l < nav.l - 1) problems.push(name + ' leaves the switcher');
  return {
    problems,
    groups: groups.length,
    labels: groups.map(g => g.querySelector('.board-tabs__label').textContent.trim()),
    scrollOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    gap: groups.length > 1 ? rect(groups[1]).l - rect(groups[0]).r : null,
    sameRow: groups.length > 1 ? Math.abs(rect(groups[1]).t - rect(groups[0]).t) < 2 : null,
  };
}"""


@pytest.mark.parametrize("width", WIDTHS)
def test_labels_are_whole_and_nothing_overlaps(page: Page, width: int) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    page.goto("/leaderboards/")

    result = page.evaluate(MEASURE)

    assert result["problems"] == []
    assert [label.lower() for label in result["labels"]] == ["air", "ground", "general", "ironman"]
    assert result["scrollOverflow"] <= 0
    if width >= 1280:  # side by side with a normal gap, not pushed to the far edge
        assert result["sameRow"] is True
        assert 0 <= result["gap"] <= 80
