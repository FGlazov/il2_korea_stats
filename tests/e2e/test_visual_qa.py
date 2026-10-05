"""Visual QA of every public page and the admin branding pages (maintainer feedback 2026-10-04: features landed
without anyone looking at them in a browser).

Each page is rendered at phone, tablet and desktop width, in light and dark mode. The test saves a full-page
screenshot of each to a scratch directory (`IL2KS_QA_SHOTS`, default `<tmp>/il2ks-visual-qa`; never committed) and
asserts generic layout invariants, so a page that breaks says which, at which width and theme:

- no horizontal page scroll;
- no text clipped by an `overflow:hidden` box (own scrollWidth, or an ancestor's clip edge cutting the text);
- no overlapping interactive elements (links, buttons, labels, inputs, summaries);
- WCAG contrast of every piece of text against its composited background (4.5:1; 3:1 for large text);
- every image has an `alt` and loaded.

Console errors fail through the autouse `console_errors` fixture. Elements that scroll on purpose
(`overflow:auto|scroll`, e.g. a wide table in its wrapper) are not failures. A second pass with every `<details>`
(Columns, language menu) opened screenshots the open state and re-checks everything except overlap (an open menu lies
on top of the page by design).
"""

import os
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest
from playwright.sync_api import Page

from tests.e2e.helpers import wait_until_settled
from tests.e2e.world import QA_ADMIN, World

WIDTHS = (360, 768, 1280)
SCHEMES = ("light", "dark")
CONTRAST_AA = 4.5
CONTRAST_AA_LARGE = 3.0

AUDIT_JS = r"""({checkOverlap}) => {
  const problems = [];
  const add = (kind, msg) => { if (!problems.some(p => p.msg === msg)) problems.push({kind, msg}); };
  const label = el => {
    const text = (el.innerText || el.getAttribute('aria-label') || el.alt || el.value || '').trim().replace(/\s+/g, ' ').slice(0, 40);
    return el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).join('.') : '') + (text ? ' "' + text + '"' : '') + (el.getAttribute('href') ? ' [' + el.getAttribute('href').replace(/[?&]theme=\w+/, '').slice(0, 40) + ']' : '');
  };
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    const closed = el.closest('details:not([open])');  // Chromium still reports boxes for a closed <details>' content
    if (closed && !(el.tagName === 'SUMMARY' && el.parentElement === closed) && !(el.closest('summary') && el.closest('summary').parentElement === closed)) return false;
    return r.width > 1 && r.height > 1 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const rect = el => el.getBoundingClientRect();

  // 1. horizontal page scroll
  const doc = document.documentElement;
  if (doc.scrollWidth > doc.clientWidth + 1) {
    const culprits = [...document.body.querySelectorAll('*')].filter(el => {
      if (!visible(el) || rect(el).right <= doc.clientWidth + 1) return false;
      for (let a = el.parentElement; a && a !== document.body; a = a.parentElement)
        if (['auto', 'scroll', 'hidden', 'clip'].includes(getComputedStyle(a).overflowX)) return false;
      return true;
    }).slice(0, 4).map(label);
    add('hscroll', 'page scrolls horizontally by ' + (doc.scrollWidth - doc.clientWidth) + 'px; wide: ' + culprits.join(' | '));
  }

  // 2. clipped text
  const hasOwnText = el => [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
  const textEls = [...document.body.querySelectorAll('*')].filter(el =>
    hasOwnText(el) && !['SCRIPT', 'STYLE', 'OPTION', 'TITLE'].includes(el.tagName) && visible(el));
  for (const el of textEls) {
    const s = getComputedStyle(el);
    if (['hidden', 'clip'].includes(s.overflowX) && el.scrollWidth > el.clientWidth + 1)
      add('clipped', 'text cut off by its own overflow: ' + label(el) + ' (' + el.scrollWidth + ' > ' + el.clientWidth + ')');
    if (['TD', 'TH', 'BUTTON', 'LABEL', 'SUMMARY', 'LI'].includes(el.tagName) || el.classList.contains('badge') || el.matches('.board-tabs__label, .stat-tile *'))
      if (s.overflowX === 'visible' && el.clientWidth > 0 && el.scrollWidth > el.clientWidth + 1)
        add('spill', 'content spills out of its box: ' + label(el) + ' (' + el.scrollWidth + ' > ' + el.clientWidth + ')');
    const r = rect(el);
    for (let a = el.parentElement; a && a !== document.documentElement; a = a.parentElement) {
      const as = getComputedStyle(a);
      if (!['hidden', 'clip'].includes(as.overflowX)) continue;
      const ar = rect(a);
      if (ar.width > 1 && (r.right > ar.right + 1.5 || r.left < ar.left - 1.5))
        add('clipped', 'text cut off by ' + label(a) + ': ' + label(el));
    }
  }

  // 3. overlapping interactive elements
  if (checkOverlap) {
    const sel = 'a[href], button, input:not([type=hidden]), select, textarea, summary, label';
    const items = [...document.body.querySelectorAll(sel)].filter(el => {
      if (!visible(el)) return false;
      const s = getComputedStyle(el);
      return s.position !== 'fixed' && s.position !== 'sticky';
    });
    const boxes = el => [...el.getClientRects()].filter(r => r.width > 1 && r.height > 1);  // one per line of a wrapped inline link
    for (let i = 0; i < items.length; i++) {
      const a = items[i], ra = boxes(a);
      for (let j = i + 1; j < items.length; j++) {
        const b = items[j];
        if (a.contains(b) || b.contains(a)) continue;
        if (a.classList.contains('stretched-link') || b.classList.contains('stretched-link')) continue;
        if (a.tagName === 'LABEL' && a.control === b || b.tagName === 'LABEL' && b.control === a) continue;
        const rb = boxes(b);
        for (const x of ra) for (const y of rb) {
          const w = Math.min(x.right, y.right) - Math.max(x.left, y.left);
          const h = Math.min(x.bottom, y.bottom) - Math.max(x.top, y.top);
          if (w > 2 && h > 2) add('overlap', label(a) + ' overlaps ' + label(b) + ' (' + Math.round(w) + 'x' + Math.round(h) + 'px at ' + [x, y].map(r => Math.round(r.left) + ',' + Math.round(r.top) + '-' + Math.round(r.right) + ',' + Math.round(r.bottom)).join(' / ') + ')');
        }
      }
    }
  }

  // 4. contrast
  const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
  const ctx = canvas.getContext('2d', {willReadFrequently: true});
  const rgba = css => {
    ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = '#000'; ctx.fillStyle = css; ctx.fillRect(0, 0, 1, 1);
    const d = ctx.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255];
  };
  const over = (top, under) => {
    const a = top[3] + under[3] * (1 - top[3]);
    return a === 0 ? [0, 0, 0, 0] : [0, 1, 2].map(i => (top[i] * top[3] + under[i] * under[3] * (1 - top[3])) / a).concat([a]);
  };
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const background = el => {
    let acc = [0, 0, 0, 0];
    for (let a = el; a; a = a.parentElement) {
      const s = getComputedStyle(a);
      if (s.backgroundImage !== 'none') return null;  // gradient / image: not computable
      acc = over(acc, rgba(s.backgroundColor));
      if (acc[3] >= 0.999) return acc;
    }
    return over(acc, [255, 255, 255, 1]);
  };
  const seen = new Set();
  for (const el of textEls) {
    if (el.closest('[disabled], [aria-disabled=true]') || el.tagName === 'INPUT') continue;
    const s = getComputedStyle(el);
    if (s.webkitTextFillColor && s.webkitTextFillColor !== s.color && s.webkitTextFillColor.includes('0, 0, 0, 0')) continue;
    const bg = background(el);
    if (!bg) continue;
    let fg = rgba(s.color), opacity = 1;
    for (let a = el; a; a = a.parentElement) opacity *= parseFloat(getComputedStyle(a).opacity);
    fg[3] *= opacity;
    fg = over(fg, bg);
    const size = parseFloat(s.fontSize), bold = parseInt(s.fontWeight) >= 700;
    const need = size >= 24 || (size >= 18.66 && bold) ? 3 : 4.5;
    const got = ratio(fg, bg);
    if (got < need) {
      const key = s.color + '|' + bg.map(Math.round).join(',') + '|' + el.className;
      if (seen.has(key)) continue;
      seen.add(key);
      add('contrast', 'contrast ' + got.toFixed(2) + ' < ' + need + ': ' + label(el) + ' (' + s.color + ' on rgb(' + bg.slice(0, 3).map(Math.round).join(',') + '))');
    }
  }

  // 6. stray list markers: the list says `list-style: none`, Pico's rule on its <li> brings a bullet back
  for (const li of document.querySelectorAll('ul > li, ol > li')) {
    const list = li.parentElement;
    if (getComputedStyle(list).listStyleType === 'none' && getComputedStyle(li).listStyleType !== 'none' && getComputedStyle(li).display === 'list-item' && visible(li))
      add('marker', 'stray list marker: ' + label(list) + ' > li');
  }

  // 5. images
  for (const img of document.images) {
    if (!img.hasAttribute('alt')) add('img', 'image without alt: ' + (img.getAttribute('src') || '').slice(0, 80));
    else if (img.complete && img.naturalWidth === 0 && visible(img)) add('img', 'image did not load: ' + img.getAttribute('src'));
  }
  return problems;
}"""  # noqa: E501

OPEN_DETAILS_JS = "() => document.querySelectorAll('details').forEach(d => { d.open = true; })"
SCROLLERS_JS = """() => [...document.querySelectorAll('main *')].filter(el => {
  const s = getComputedStyle(el);
  return ['auto', 'scroll'].includes(s.overflowX) && el.scrollWidth > el.clientWidth + 1 && el.getBoundingClientRect().width > 1;
}).map(el => el.tagName.toLowerCase() + '.' + String(el.className).trim().split(/\\s+/).join('.'))"""  # noqa: E501


def shots_dir() -> Path:
    path = Path(os.environ.get("IL2KS_QA_SHOTS") or Path(tempfile.gettempdir()) / "il2ks-visual-qa")
    path.mkdir(parents=True, exist_ok=True)
    return path


def with_theme(url: str, scheme: str) -> str:
    return f"{url}{'&' if '?' in url else '?'}theme={scheme}"


def first_href(page: Page, start: str, selector: str) -> str:
    page.goto(start)
    href = page.eval_on_selector(selector, "el => el.getAttribute('href')")
    assert href, f"no {selector} on {start}"
    return str(href)


type Resolve = Callable[[Page, World], str]


def _const(url: str) -> Resolve:
    return lambda page, world: url


def _ace(path: str) -> Resolve:
    return lambda page, world: f"/players/{world.ace_pk}/{path}"


def _tour_of_ace(page: Page, world: World) -> str:
    profile = f"/players/{world.ace_pk}/"
    href = first_href(page, profile, "a[href*='tour=']:not([href*='tour=all'])")
    return profile + href[href.index("?") :] if href.startswith("?") else href


def _first_aircraft(page: Page, world: World) -> str:
    return first_href(page, "/aircraft/", "main a[href^='/aircraft/']:not([href='/aircraft/'])")


def _aircraft_intercept(page: Page, world: World) -> str:
    return _first_aircraft(page, world) + "?intercept=1"


def _first_achievement(page: Page, world: World) -> str:
    return first_href(page, "/achievements/", "main a[href^='/achievements/']:not([href='/achievements/'])")


PAGES: dict[str, Resolve] = {
    "home": _const("/"),
    "missions": _const("/missions/"),
    "missions-all-tours": _const("/missions/?tour=all"),
    "mission": lambda page, world: f"/missions/{world.featured_mission_pk}/",
    "mission-real-log": lambda page, world: f"/missions/{world.logs_mission_pk}/",
    "players": _const("/players/"),
    "players-search": _const("/players/?q=Ace"),
    "player": _ace(""),
    "player-tour": _tour_of_ace,
    "player-real-log": lambda page, world: f"/players/{world.logs_player_pk}/",
    "player-sorties": _ace("sorties/"),
    "player-killboard": _ace("killboard/"),
    "player-streaks": _ace("streaks/"),
    "player-achievements": _ace("achievements/"),
    "ironman": _const("/leaderboards/ironman-air/"),
    "ironman-ground": _const("/leaderboards/ironman-ground/"),
    "achievements": _const("/achievements/"),
    "achievement-holders": _first_achievement,
    "sortie": lambda page, world: f"/sorties/{world.ace_sortie_pk}/",
    "sortie-shot-down": lambda page, world: f"/sorties/{world.delta_sortie_pk}/",
    "sortie-real-log": lambda page, world: f"/sorties/{world.logs_sortie_pk}/",
    "aircraft": _const("/aircraft/"),
    "aircraft-detail": _first_aircraft,
    "aircraft-detail-intercept": _aircraft_intercept,
    "leaderboards": _const("/leaderboards/"),
    "leaderboard-ground": _const("/leaderboards/ground/"),
    "leaderboard-interception": _const("/leaderboards/interception/"),
    "leaderboard-tank-busting": _const("/leaderboards/tank-busting/"),
    "styleguide": _const("/_styleguide/"),
}


def audit_page(page: Page, name: str, url: str, shots: Path) -> list[str]:
    """Render `url` at every width and scheme, screenshot it, return the invariant violations as text lines."""
    found: list[str] = []
    notes: list[str] = []
    for scheme in SCHEMES:
        page.emulate_media(color_scheme=scheme)
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(with_theme(url, scheme))
            wait_until_settled(page)
            tag = f"{name}-{width}-{scheme}"
            page.screenshot(path=str(shots / f"{tag}.png"), full_page=True)
            for problem in page.evaluate(AUDIT_JS, {"checkOverlap": True}):
                found.append(f"[{name} {width}px {scheme}] {problem['kind']}: {problem['msg']}")
            for scroller in page.evaluate(SCROLLERS_JS):
                notes.append(f"[{name} {width}px {scheme}] scrolls sideways: {scroller}")
            if page.locator("details").count():
                page.evaluate(OPEN_DETAILS_JS)
                page.wait_for_timeout(450)  # Pico fades a dropdown in; measure the finished state
                page.screenshot(path=str(shots / f"{tag}-open.png"), full_page=True)
                for problem in page.evaluate(AUDIT_JS, {"checkOverlap": False}):
                    found.append(f"[{name} {width}px {scheme} open] {problem['kind']}: {problem['msg']}")
    if notes:
        print("\n".join(sorted(set(notes))))
    return sorted(set(found))


@pytest.mark.parametrize("name", PAGES)
def test_public_page_layout(page: Page, world: World, name: str) -> None:
    """One public page, six renderings: see the module docstring for the invariants."""
    shots = shots_dir()
    url = PAGES[name](page, world)
    response = page.goto(url)
    assert response is not None
    assert response.status == 200, f"{url} answered {response.status}"

    violations = audit_page(page, name, url, shots)

    assert not violations, f"{url}: screenshots in {shots}\n" + "\n".join(violations)


def test_a_phone_timeline_time_cell_wraps_instead_of_spilling(page: Page, world: World) -> None:
    """CI regression (2026-10-05): the sticky first column is capped at 40vw on a phone; the timeline's time cell
    (nowrap, "+2:27:53 12:30:33 PM") was wider than that with Linux fonts and spilled. It may wrap."""
    page.set_viewport_size({"width": 360, "height": 800})
    page.goto(f"/sorties/{world.logs_sortie_pk}/")
    cell = page.locator("table.timeline tbody tr > td:first-child").first
    assert cell.evaluate("el => getComputedStyle(el).whiteSpace") == "normal"


def login(page: Page) -> None:
    page.goto("/admin/login/")
    page.get_by_label("Username").fill(QA_ADMIN[0])
    page.get_by_label("Password").fill(QA_ADMIN[1])
    page.get_by_role("button", name="Log in").click()
    page.wait_for_url("**/admin/")


ADMIN_PAGES = {
    "admin-index": "/admin/",
    "admin-site-settings": "/admin/il2ks_db/sitesettings/",
    "admin-ingestion": "/admin/ingestion/",
}


@pytest.mark.parametrize("name", ADMIN_PAGES)
def test_admin_page_layout(page: Page, name: str) -> None:
    """The admin pages (branding editor: theme editor, nav links, fonts). Admin has its own theme switch, so only the
    colour scheme of the browser changes."""
    shots = shots_dir()
    login(page)
    violations: list[str] = []
    for scheme in SCHEMES:
        page.emulate_media(color_scheme=scheme)
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(ADMIN_PAGES[name])
            wait_until_settled(page)
            page.screenshot(path=str(shots / f"{name}-{width}-{scheme}.png"), full_page=True)
            violations += [
                f"[{name} {width}px {scheme}] {p['kind']}: {p['msg']}"
                for p in page.evaluate(AUDIT_JS, {"checkOverlap": True})
            ]
    assert not violations, f"screenshots in {shots}\n" + "\n".join(sorted(set(violations)))
