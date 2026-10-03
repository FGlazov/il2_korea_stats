"""`il2ks run` supervisor (doc 04 process model): restarts with backoff, clean shutdown, with fake children."""

import logging
import sys
import threading
from collections.abc import Callable

import pytest

from il2ks.serving.supervisor import Backoff, ChildSpec, Supervisor, clean_output_line


class FakeChild:
    """A process that runs until `exit_code` is set; records how it was asked to stop."""

    _next_pid = 1000

    def __init__(self, spec: ChildSpec, *, stops_when_asked: bool = True) -> None:
        FakeChild._next_pid += 1
        self.pid = FakeChild._next_pid
        self.spec = spec
        self.exit_code: int | None = None
        self.stop_called = False
        self.killed = False
        self.stops_when_asked = stops_when_asked

    def poll(self) -> int | None:
        return self.exit_code

    def stop(self) -> None:
        self.stop_called = True
        if self.stops_when_asked:
            self.exit_code = 0

    def kill(self) -> None:
        self.killed = True
        self.exit_code = -9


class World:
    """Fake clock + spawner: `sleep` advances time."""

    def __init__(
        self, names: tuple[str, ...] = ("web",), *, grace_s: float = 15.0, backoff: Backoff | None = None
    ) -> None:
        self.now = 0.0
        self.stop = threading.Event()
        self.children: dict[str, list[FakeChild]] = {name: [] for name in names}
        self.hang: set[str] = set()
        self.fail_to_start: set[str] = set()
        self.changes: list[dict[str, int]] = []
        self.sup = Supervisor(
            [ChildSpec(name, [name]) for name in names],
            self.spawn,
            self.stop,
            lambda: self.now,
            self.sleep,
            backoff=backoff or Backoff(),
            grace_s=grace_s,
            on_change=self.changes.append,
        )

    def spawn(self, spec: ChildSpec) -> FakeChild:
        if spec.name in self.fail_to_start:
            raise FileNotFoundError(spec.command[0])
        child = FakeChild(spec, stops_when_asked=spec.name not in self.hang)
        self.children[spec.name].append(child)
        return child

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def advance(self, seconds: float) -> None:
        """Move the clock and let the supervisor look, in 0.5 s steps."""
        end = self.now + seconds
        while self.now < end:
            self.sup.tick()
            self.now += 0.5

    def last(self, name: str) -> FakeChild:
        return self.children[name][-1]


def test_all_children_start_once_and_changes_are_reported() -> None:
    w = World(("web", "watch", "caddy"))
    w.sup.tick()
    assert {n: len(c) for n, c in w.children.items()} == {"web": 1, "watch": 1, "caddy": 1}
    assert list(w.changes[-1]) == ["web", "watch", "caddy"]
    w.sup.tick()
    assert {n: len(c) for n, c in w.children.items()} == {"web": 1, "watch": 1, "caddy": 1}  # nothing restarted


def test_a_crashed_child_is_restarted_after_the_backoff() -> None:
    w = World()
    w.sup.tick()
    w.last("web").exit_code = 1
    w.sup.tick()
    assert len(w.children["web"]) == 1  # exit seen, restart scheduled 1 s later
    w.advance(0.4)
    assert len(w.children["web"]) == 1
    w.advance(1.5)
    assert len(w.children["web"]) == 2


def test_backoff_doubles_up_to_the_maximum_and_resets_after_a_stable_run(caplog: pytest.LogCaptureFixture) -> None:
    w = World(backoff=Backoff(initial_s=1, factor=2, max_s=8, stable_after_s=60))
    w.sup.tick()
    delays: list[float] = []
    for _ in range(6):
        w.last("web").exit_code = 1
        before = w.now
        # step until a new child appears
        count = len(w.children["web"])
        while len(w.children["web"]) == count:
            w.sup.tick()
            w.now += 0.25
        delays.append(round(w.now - before - 0.25))
    assert delays == [1, 2, 4, 8, 8, 8]

    # now a child that stays up for a minute: the next failure starts the delay over
    w.now += 61
    w.last("web").exit_code = 1
    before = w.now
    count = len(w.children["web"])
    with caplog.at_level(logging.WARNING, logger="il2ks.run"):
        while len(w.children["web"]) == count:
            w.sup.tick()
            w.now += 0.25
    assert round(w.now - before - 0.25) == 1


def test_a_child_that_exits_with_zero_is_restarted_too() -> None:
    w = World()
    w.sup.tick()
    w.last("web").exit_code = 0
    w.advance(3)
    assert len(w.children["web"]) == 2


def test_one_crashing_child_does_not_disturb_the_others() -> None:
    w = World(("web", "caddy"))
    w.sup.tick()
    caddy = w.last("caddy")
    w.last("web").exit_code = 1
    w.advance(3)
    assert len(w.children["web"]) == 2
    assert w.children["caddy"] == [caddy]
    assert caddy.exit_code is None


def test_a_child_that_cannot_be_started_is_retried_with_backoff() -> None:
    w = World()
    w.fail_to_start.add("web")
    w.advance(4)
    assert w.children["web"] == []
    w.fail_to_start.clear()
    w.advance(10)
    assert len(w.children["web"]) == 1


def test_watch_finding_the_writer_lock_busy_is_not_logged_as_an_error(caplog: pytest.LogCaptureFixture) -> None:
    w = World(("web", "watch"))
    w.sup.tick()
    with caplog.at_level(logging.INFO, logger="il2ks.run"):
        w.last("watch").exit_code = 3
        w.sup.tick()
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("writer busy" in r.getMessage() for r in caplog.records)


def test_a_crash_is_logged_with_the_exit_code(caplog: pytest.LogCaptureFixture) -> None:
    w = World()
    w.sup.tick()
    with caplog.at_level(logging.INFO, logger="il2ks.run"):
        w.last("web").exit_code = 7
        w.sup.tick()
    assert any("web exited with code 7" in r.getMessage() and r.levelno == logging.ERROR for r in caplog.records)


def test_shutdown_asks_in_reverse_order_and_does_not_kill_cooperative_children() -> None:
    w = World(("web", "watch", "caddy"))
    w.sup.tick()
    order: list[str] = []
    for name in w.children:
        child = w.last(name)
        original = child.stop

        def recording_stop(n: str = name, o: Callable[[], None] = original) -> None:
            order.append(n)
            o()

        child.stop = recording_stop
    w.sup.shutdown()
    assert order == ["caddy", "watch", "web"]
    assert all(not w.last(n).killed for n in w.children)
    assert w.sup.pids() == {}


def test_a_child_that_ignores_the_request_is_killed_after_the_grace_period() -> None:
    w = World(("web", "watch"), grace_s=5.0)
    w.hang.add("web")
    w.sup.tick()
    start = w.now
    w.sup.shutdown()
    assert w.last("web").stop_called
    assert w.last("web").killed
    assert not w.last("watch").killed
    assert 5.0 <= w.now - start < 6.0


def test_run_stops_children_when_the_stop_event_is_set() -> None:
    w = World(("web", "watch"))
    ticks = 0

    def sleep(seconds: float) -> None:
        nonlocal ticks
        ticks += 1
        w.now += seconds
        if ticks == 3:
            w.stop.set()

    w.sup.sleep = sleep
    w.sup.run()
    assert all(c[0].stop_called for c in w.children.values())
    assert all(c[0].exit_code == 0 for c in w.children.values())


def test_no_restart_is_attempted_after_stop_is_requested() -> None:
    w = World()
    w.sup.tick()
    w.last("web").exit_code = 1
    w.sup.tick()
    w.stop.set()
    w.advance(5)
    assert len(w.children["web"]) == 1


def test_run_still_stops_children_if_the_loop_raises() -> None:
    w = World()
    calls = 0

    def sleep(seconds: float) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt

    w.sup.sleep = sleep
    with pytest.raises(KeyboardInterrupt):
        w.sup.run()
    assert w.children["web"][0].stop_called


# --- output forwarding ------------------------------------------------------------------------------------------------


def test_clean_output_line_strips_colour_and_unprintable_characters(monkeypatch: pytest.MonkeyPatch) -> None:
    class Stdout:
        encoding = "cp1252"

    monkeypatch.setattr(sys, "stdout", Stdout())
    line = "2026/10/03 15:51:31\t\x1b[33mWARN\x1b[0m\texiting; byeee!! \U0001f44b\t{}\r\n"
    assert clean_output_line(line) == "2026/10/03 15:51:31\tWARN\texiting; byeee!! ?\t{}"
