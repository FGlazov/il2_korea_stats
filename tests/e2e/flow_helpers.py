"""Shared by the user-flow tests (`test_flows_*.py`): tables read by their headers, a patient timeout fixture, and the
admin login. Like `helpers.py`, nothing here knows a CSS class, except the table markup (`thead th` / `tbody tr`)."""

import re
from typing import cast

import pytest
from playwright.sync_api import Locator, Page, expect

from tests.e2e.helpers import main_region
from tests.e2e.world import QA_ADMIN

PATIENT_MS = 20_000
"""The suite runs on loaded machines (agents, CI): a flow clicks through many pages, one slow answer is no bug."""

TABLE_ROWS = r"""table => {
  const text = el => el.textContent.replace(/\s+/g, " ").trim();
  // a header's description (role=tooltip) and its "?" marker are not part of its label
  const label = th => {
    const copy = th.cloneNode(true);
    copy.querySelectorAll("[role=tooltip], .col-hint__marker").forEach(e => e.remove());
    return text(copy).replace(/\s+[^\w\s]$/, "");
  };
  const headers = [...table.querySelectorAll("thead th")].map(label);
  return [...table.querySelectorAll('tbody tr')].map(tr => {
    const row = {};
    [...tr.children].forEach((cell, i) => { row[headers[i] ?? String(i)] = text(cell); });
    return row;
  });
}"""


@pytest.fixture
def patient_timeouts(page: Page) -> None:
    """Generous timeouts for the long flows (set after conftest's autouse fixture, so this one wins)."""
    expect.set_options(timeout=PATIENT_MS)
    page.set_default_timeout(PATIENT_MS)
    page.set_default_navigation_timeout(PATIENT_MS)


def table_with(page: Page, *headers: str) -> Locator:
    """The table of the page's main region that has all these column headers (found by what it shows, not by class)."""
    table = main_region(page).get_by_role("table")
    for header in headers:
        table = table.filter(has=page.get_by_role("columnheader", name=header_name(header)))
    return table.first


def header_name(label: str) -> re.Pattern[str]:
    """A sortable column header's accessible name is its label plus a one-character sort arrow ("Pilots ↕"): match
    the label exactly, with or without the arrow (so "Air kills" does not match "Air kills (PvP)"). While the pointer or
    focus is on a header with a description, the shown description is part of the name too: allowed after the arrow."""
    escaped = re.escape(label).replace("/", "\\/")  # Playwright prints a regex into its selector without escaping "/"
    return re.compile(rf"^{escaped}(\s+[↕▲▼](\s.*)?)?\s*$", re.S)


def settle(page: Page) -> None:
    """Wait until the htmx swap that follows a change has landed, before reading a table or changing the next control.

    Not `wait_for_load_state("networkidle")`: that state is reached once, at the first load, and later htmx requests
    don't reset it, so it returned at once and a control changed mid-swap was replaced unchanged (CI, 2026-10-05)."""
    page.wait_for_function(
        "document.readyState === 'complete' && !document.querySelector('.htmx-request, .htmx-swapping, .htmx-settling')"
    )


def column_header(page: Page, label: str) -> Locator:
    return page.get_by_role("columnheader", name=header_name(label))


def rows_of(table: Locator) -> list[dict[str, str]]:
    """Every body row as {header text: cell text}: assert on what a column says, whatever its position."""
    return cast(list[dict[str, str]], table.evaluate(TABLE_ROWS))


def number(text: str) -> float:
    """ "1,547" -> 1547.0, "5.00" -> 5.0, "95%" -> 95.0; "—" (no value) -> nan."""
    match = re.search(r"-?\d[\d,]*(\.\d+)?", text)
    return float("nan") if match is None else float(match.group(0).replace(",", ""))


def row_for(rows: list[dict[str, str]], label: str, column: str) -> dict[str, str]:
    """The row whose `column` cell reads exactly `label` (a player, an aircraft); fails with what there is."""
    for row in rows:
        if name_of(row.get(column, "")) == label:
            return row
    raise AssertionError(f"no row with {column}={label!r}; rows: {names_in(rows, column)}")


def name_of(cell: str) -> str:
    """A name cell may carry a badge after the name ("MiG-15bis Jet"): the name alone."""
    return re.sub(r"\s+(Jet|Prop)$", "", cell.strip())


def names_in(rows: list[dict[str, str]], column: str) -> list[str]:
    return [name_of(row.get(column, "")) for row in rows]


def admin_login(page: Page) -> None:
    page.goto("/admin/login/")
    page.get_by_label("Username").fill(QA_ADMIN[0])
    page.get_by_label("Password").fill(QA_ADMIN[1])
    page.get_by_role("button", name=re.compile("log in", re.IGNORECASE)).click()
    page.wait_for_url("**/admin/")


def tour_select(page: Page) -> Locator:
    """The page's tour dropdown (a labelled combobox)."""
    return main_region(page).get_by_role("combobox", name=re.compile("^Tour"))


def tab_to(page: Page, selector: str, limit: int = 120) -> None:
    """Press Tab until the element matching `selector` has focus; fail if it is never reached."""
    for _ in range(limit):
        if page.evaluate("sel => !!document.activeElement && document.activeElement.matches(sel)", selector):
            return
        page.keyboard.press("Tab")
    raise AssertionError(f"Tab never reached {selector}")


def no_horizontal_scroll(page: Page) -> bool:
    """The document is not wider than the window (tables may scroll inside their own box)."""
    return bool(page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"))
