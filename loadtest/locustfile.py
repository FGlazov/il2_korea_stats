"""Locust load test for the public site (doc 08, docs/performance-testing.md).

Run it against a local `il2ks web` with a seeded database; never against somebody else's server:

    uv run locust -f loadtest/locustfile.py --host http://127.0.0.1:8000 --headless -u 20 -r 5 -t 60s

The test needs no seed file: when it starts it crawls the lists (missions, players, aircraft) for the ids it will
visit, so it works on any database, including a copy of a real one. Four kinds of visitors, with realistic flows:

- Browser: home -> missions -> a mission -> a sortie of that mission page.
- Searcher: search a name -> profile -> the player's sorties -> one sortie.
- Aircraft fan: aircraft list -> one type, sometimes with a sort.
- Watcher: keeps the home page open, so its HTMX `/live/` fragment is polled (FR-ING-12).
- Returning: revalidates pages it saw with `If-None-Match` (TD-28), which should be answered 304.

Every new visitor also fetches the page's stylesheets and scripts once, like a cold browser. At the end the run prints a
short markdown report (requests, failures, RPS, p50 / p95 / max per endpoint)."""

import random
import re
from urllib.parse import urljoin

import gevent
from locust import HttpUser, between, events, task
from locust.env import Environment
from locust.runners import MasterRunner, WorkerRunner

LIVE_POLL_SECONDS = 5
"""How often an open home page polls `/live/` (the template's `hx-trigger`); keep in sync with the template."""
SEARCH_TERMS = ("Pilot", "Ace", "a", "e", "Bob", "x")
SORTS = ("", "?sort=-sorties", "?sort=-kills", "?sort=name")

_STATIC_RE = re.compile(r"""(?:href|src)="(/static/[^"]+\.(?:css|js|woff2|svg))\"""")
_IDS: dict[str, list[int]] = {"missions": [], "players": [], "aircraft": [], "sorties": []}
_NAMES: list[str] = []


def _ids(html: str, kind: str) -> list[int]:
    return [int(m) for m in dict.fromkeys(re.findall(rf'href="/{kind}/(\d+)/"', html))]


def _pick(kind: str) -> int | None:
    pool = _IDS[kind]
    return random.choice(pool) if pool else None


@events.test_start.add_listener
def discover(environment: Environment, **_kwargs: object) -> None:
    """Crawl the lists once for ids to visit (on the master of a distributed run, there is nothing to do)."""
    if isinstance(environment.runner, MasterRunner) or environment.host is None:
        return
    import urllib.request

    def fetch(path: str) -> str:
        assert environment.host is not None
        with urllib.request.urlopen(urljoin(environment.host, path), timeout=30) as response:
            return str(response.read().decode("utf-8", "replace"))

    for path, kind in (("/missions/", "missions"), ("/players/", "players"), ("/aircraft/", "aircraft")):
        _IDS[kind] = _ids(fetch(path), kind)
    for player in _IDS["players"][:5]:
        _IDS["sorties"] += _ids(fetch(f"/players/{player}/sorties/"), "sorties")
    for mission in _IDS["missions"][:3]:
        _IDS["sorties"] += _ids(fetch(f"/missions/{mission}/"), "sorties")
    empty = [kind for kind, pool in _IDS.items() if not pool]
    if empty:
        raise SystemExit(f"loadtest: no {', '.join(empty)} found on {environment.host}: is the database seeded?")


class Visitor(HttpUser):
    """Base class: not run itself (abstract); fetches the static files of the first page like a cold browser."""

    abstract = True
    wait_time = between(1, 4)

    def on_start(self) -> None:
        self.etags: dict[str, str] = {}
        with self.client.get("/", name="/", catch_response=True) as response:
            for path in dict.fromkeys(_STATIC_RE.findall(response.text)):
                self.client.get(path, name="/static/*")

    def page(self, path: str, name: str | None = None, headers: dict[str, str] | None = None) -> str:
        """GET a page; remembers its ETag. Fails the request when the status isn't 200."""
        with self.client.get(path, name=name or path, catch_response=True, headers=headers) as response:
            if response.status_code != 200:
                response.failure(f"status {response.status_code}")
                return ""
            if "ETag" in response.headers:
                self.etags[path] = response.headers["ETag"]
            return response.text


class Browser(Visitor):
    weight = 5

    @task
    def home_to_sortie(self) -> None:
        self.page("/", "/")
        self.page("/missions/", "/missions/")
        mission = _pick("missions")
        html = self.page(f"/missions/{mission}/", "/missions/[id]/")
        sorties = _ids(html, "sorties")
        if sorties:
            self.page(f"/sorties/{random.choice(sorties)}/", "/sorties/[id]/")
        if random.random() < 0.3:
            self.page("/missions/?page=2", "/missions/?page=[n]")


class Searcher(Visitor):
    weight = 3

    @task
    def search_to_sortie(self) -> None:
        html = self.page(f"/players/?q={random.choice(SEARCH_TERMS)}", "/players/?q=[term]")
        players = _ids(html, "players") or [p for p in [_pick("players")] if p]
        player = random.choice(players)
        self.page(f"/players/{player}/", "/players/[id]/")
        html = self.page(f"/players/{player}/sorties/", "/players/[id]/sorties/")
        sorties = _ids(html, "sorties")
        if sorties:
            self.page(f"/sorties/{random.choice(sorties)}/", "/sorties/[id]/")
        if random.random() < 0.3:
            self.page(f"/players/{player}/killboard/", "/players/[id]/killboard/")


class AircraftFan(Visitor):
    weight = 1

    @task
    def aircraft(self) -> None:
        self.page(f"/aircraft/{random.choice(SORTS)}", "/aircraft/")
        html = self.page("/aircraft/", "/aircraft/")
        types = _ids(html, "aircraft")
        self.page(f"/aircraft/{random.choice(types) if types else _pick('aircraft')}/", "/aircraft/[id]/")
        if random.random() < 0.3:
            self.page("/streaks/", "/streaks/")


class Watcher(Visitor):
    """Leaves the home page open: the browser polls the `/live/` fragment (own max-age, FR-ING-12, FR-ING-15)."""

    weight = 2
    wait_time = between(LIVE_POLL_SECONDS, LIVE_POLL_SECONDS + 1)

    @task
    def poll_live(self) -> None:
        self.page("/live/", "/live/", headers={"HX-Request": "true"})


class Returning(Visitor):
    """Comes back to pages it has seen: conditional GETs, which the data-version cache answers with 304 (TD-28)."""

    weight = 3

    @task
    def revalidate(self) -> None:
        for path in ("/", "/missions/", "/players/", "/aircraft/"):
            self.page(path, path)
        for path, etag in list(self.etags.items()):
            with self.client.get(
                path, name=f"{path} (304)", headers={"If-None-Match": etag}, catch_response=True
            ) as response:
                if response.status_code != 304:
                    response.failure(f"expected 304, got {response.status_code}")


@events.quitting.add_listener
def print_report(environment: Environment, **_kwargs: object) -> None:
    """A short markdown table, so a run can be pasted into an issue or compared by eye with the last one."""
    if isinstance(environment.runner, WorkerRunner):
        return
    gevent.sleep(0)
    stats = environment.stats
    rows = sorted(stats.entries.values(), key=lambda e: -e.num_requests)
    lines = [
        "",
        "| endpoint | requests | fails | RPS | p50 ms | p95 ms | max ms |",
        "|---|--:|--:|--:|--:|--:|--:|",
    ]
    for entry in [*rows, stats.total]:
        lines.append(
            f"| {entry.name if entry is not stats.total else 'TOTAL'} | {entry.num_requests} | {entry.num_failures} "
            f"| {entry.total_rps:.1f} | {entry.get_response_time_percentile(0.5):.0f} "
            f"| {entry.get_response_time_percentile(0.95):.0f} | {entry.max_response_time:.0f} |"
        )
    print("\n".join(lines))
    if stats.total.fail_ratio > 0.01:
        environment.process_exit_code = 1
