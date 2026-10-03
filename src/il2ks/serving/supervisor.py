"""`il2ks run`: start the child processes, restart them when they die, stop them all cleanly (doc 04 process model).

The `Supervisor` knows nothing about operating systems: it works on `Child` objects made by a `Spawner`, and on a clock
and sleep function it is given, so its restart and shutdown logic is tested with fake children. `spawn_subprocess` is
the real spawner.

Restarts: a child that exits is started again after a delay that doubles with every quick failure (1 s, 2 s, 4 s, ... up
to 60 s) and starts over once a child has stayed up for a minute, so a broken config doesn't spin the CPU or flood the
log, and a one-off crash is back within a second.

Shutdown: every child is asked to stop (SIGTERM; on Windows CTRL_BREAK to its own process group), given `grace_s`
seconds, then killed (the whole process tree, because the web server has worker processes).
"""

import contextlib
import logging
import os
import re
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

log = logging.getLogger("il2ks.run")

EXIT_LOCK_BUSY = 3  # il2ks.cli.EXIT_LOCKED: `watch` found another writer busy; benign, expected while `web` migrates


@dataclass(frozen=True, slots=True)
class ChildSpec:
    name: str
    command: Sequence[str]
    env: Mapping[str, str] | None = None
    cwd: Path | None = None
    forward_output: bool = False  # copy the child's output lines into our log (for children that don't log themselves)


class Child(Protocol):
    @property
    def pid(self) -> int: ...

    def poll(self) -> int | None:
        """The exit code, or None while running."""
        ...

    def stop(self) -> None:
        """Ask politely (SIGTERM / CTRL_BREAK)."""
        ...

    def kill(self) -> None:
        """Force it, and its children."""
        ...


type Spawner = Callable[[ChildSpec], Child]


@dataclass(frozen=True, slots=True)
class Backoff:
    initial_s: float = 1.0
    factor: float = 2.0
    max_s: float = 60.0
    stable_after_s: float = 60.0  # ran this long: the next failure starts the delay over


@dataclass(slots=True)
class _State:
    spec: ChildSpec
    child: Child | None = None
    started_at: float = 0.0
    next_start: float = 0.0
    delay: float = 0.0
    starts: int = 0


@dataclass(slots=True)
class Supervisor:
    specs: Sequence[ChildSpec]
    spawn: Spawner
    stop_event: threading.Event
    clock: Callable[[], float]
    sleep: Callable[[float], None]
    backoff: Backoff = field(default_factory=Backoff)
    grace_s: float = 15.0
    poll_s: float = 0.5
    on_change: Callable[[dict[str, int]], None] | None = None  # called with name -> PID whenever a child (re)starts
    restart_when: Callable[[], bool] | None = None  # polled every tick; True = stop everything and return to the caller
    _states: list[_State] = field(init=False)

    def __post_init__(self) -> None:
        self._states = [_State(spec) for spec in self.specs]

    def run(self) -> bool:
        """Supervise until `stop_event` is set (False) or `restart_when` says so (True), then stop every child.

        True means "start over with fresh specs": `il2ks run` uses it when the configuration changed (setup page)."""
        restart = False
        try:
            while not self.stop_event.is_set():
                if self.restart_when is not None and self.restart_when():
                    restart = True
                    break
                self.tick()
                self.sleep(self.poll_s)
        finally:
            self.shutdown()
        return restart

    def tick(self) -> None:
        """One pass: collect exits, schedule and perform (re)starts."""
        now = self.clock()
        changed = False
        for state in self._states:
            if state.child is not None:
                code = state.child.poll()
                if code is None:
                    continue
                self._record_exit(state, code, now)
            if state.child is None and now >= state.next_start and not self.stop_event.is_set():
                self._start(state, now)
                changed = True
        if changed and self.on_change is not None:
            self.on_change(self.pids())

    def pids(self) -> dict[str, int]:
        return {s.spec.name: s.child.pid for s in self._states if s.child is not None}

    def _start(self, state: _State, now: float) -> None:
        try:
            state.child = self.spawn(state.spec)
        except OSError as exc:
            state.delay = self._next_delay(state.delay, ran_s=0.0)
            state.next_start = now + state.delay
            log.error("could not start %s: %s; trying again in %.0f s", state.spec.name, exc, state.delay)
            return
        state.started_at = now
        state.starts += 1
        verb = "started" if state.starts == 1 else f"restarted (start no. {state.starts})"
        log.info("%s %s, PID %d", state.spec.name, verb, state.child.pid)

    def _record_exit(self, state: _State, code: int, now: float) -> None:
        ran = now - state.started_at
        state.child = None
        state.delay = self._next_delay(state.delay, ran_s=ran)
        state.next_start = now + state.delay
        name = state.spec.name
        if code == EXIT_LOCK_BUSY and name == "watch":
            log.info(
                "%s found another il2ks writer busy (exit code %d); trying again in %.0f s", name, code, state.delay
            )
        else:
            level = logging.WARNING if ran >= self.backoff.stable_after_s else logging.ERROR
            log.log(level, "%s exited with code %d after %.0f s; restarting in %.0f s", name, code, ran, state.delay)

    def _next_delay(self, previous: float, *, ran_s: float) -> float:
        if previous <= 0 or ran_s >= self.backoff.stable_after_s:
            return self.backoff.initial_s
        return min(previous * self.backoff.factor, self.backoff.max_s)

    def shutdown(self) -> None:
        """Stop all running children: ask, wait up to `grace_s`, then kill what is left."""
        running = [s for s in reversed(self._states) if s.child is not None and s.child.poll() is None]
        if not running:
            return
        log.info("stopping %s", ", ".join(s.spec.name for s in running))
        for state in running:
            assert state.child is not None
            try:
                state.child.stop()
            except OSError as exc:
                log.warning("could not ask %s to stop: %s", state.spec.name, exc)
        deadline = self.clock() + self.grace_s
        while self.clock() < deadline and any(s.child is not None and s.child.poll() is None for s in running):
            self.sleep(0.1)
        for state in running:
            assert state.child is not None
            code = state.child.poll()
            if code is None:
                log.warning("%s did not stop within %.0f s; killing it", state.spec.name, self.grace_s)
                try:
                    state.child.kill()
                except OSError as exc:
                    log.error("could not kill %s: %s", state.spec.name, exc)
            else:
                log.info("%s stopped (exit code %d)", state.spec.name, code)
            state.child = None


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def clean_output_line(line: str) -> str:
    """A child's output line for our log: no colour codes, and nothing the console's encoding can't print (Caddy ends
    its shutdown message with an emoji, which crashes a logging handler on a Windows code page)."""
    text = _ANSI_RE.sub("", line).rstrip()
    encoding = sys.stdout.encoding or "utf-8"
    return text.encode(encoding, "replace").decode(encoding, "replace")


# --- real processes ------------------------------------------------------------------------------------------------


class SubprocessChild:
    """A `subprocess.Popen` as a `Child`. On Windows it lives in its own process group so CTRL_BREAK reaches only it
    (and its own children), and Ctrl+C in our console is left to us."""

    def __init__(self, spec: ChildSpec) -> None:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        env = dict(spec.env) if spec.env is not None else None
        self._proc = subprocess.Popen(
            list(spec.command),
            env=env,
            cwd=spec.cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if spec.forward_output else None,
            stderr=subprocess.STDOUT if spec.forward_output else None,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=flags,
            start_new_session=sys.platform != "win32",
        )
        self._name = spec.name
        if spec.forward_output:
            threading.Thread(target=self._pump, name=f"{spec.name}-output", daemon=True).start()

    def _pump(self) -> None:
        out = logging.getLogger(f"il2ks.{self._name}")
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            text = clean_output_line(line)
            if text:
                out.info(text)

    @property
    def pid(self) -> int:
        return self._proc.pid

    def poll(self) -> int | None:
        return self._proc.poll()

    def stop(self) -> None:
        if sys.platform == "win32":
            try:
                os.kill(self._proc.pid, signal.CTRL_BREAK_EVENT)
            except OSError:  # no console to send it through (started without one): the polite way is not available
                self._proc.terminate()
        else:
            self._proc.terminate()

    def kill(self) -> None:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(self._proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self._proc.pid, signal.SIGKILL)


def spawn_subprocess(spec: ChildSpec) -> Child:
    return SubprocessChild(spec)
