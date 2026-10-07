"""What "Delete all data and reprocess" (`ingest.wipe`) must not lose, saved on disk before anything is deleted.

Two kinds of things live on ingested rows and would go with them:
- the admin's marks: hidden players (by account UUID), hidden missions (by server UID + mission UID), tour names and
  manual tour boundaries (`AdminMarks`);
- the primary keys the public URLs name (`/players/<pk>/`, `/missions/<pk>/`, `/sorties/<pk>/`, `?tour=<pk>`):
  account UUID -> pk, (server, mission UID) -> pk, (server, mission UID, account UUID, spawn tick) -> pk, tour start
  -> pk. The wipe rebuilds the rows from the archives, so the same stable keys come back; they are given their old pk.

The state is one JSON file in the data dir, written (and flushed) before the first row is deleted and removed when the
reprocess is over. A wipe that is killed half way leaves it: `watch` and the next `reprocess` / `ingest` find it and go
on giving the old keys, hiding and tour names to the rows that come back later. What is back already is never touched
again (an admin's unhide or rename since is final); a full resumed reprocess removes the state even if some mission
never came back.

While a state is *active* (in this process), three `pre_save` receivers give every new `Player`, `Mission` and `Tour`
its old pk and hide it when the admin had hidden it, so there is no moment when a hidden player or mission is public.
Sorties are bulk-created: `persist` asks `reserved_sortie_pks`.
"""

import json
import logging
import os
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from django.db import models
from django.db.models.signals import pre_save

from il2ks.config import Config
from il2ks.db.models import IngestRun, Mission, Player, PlayerSortie, Tour

log = logging.getLogger(__name__)

STATE_NAME = "wipe-state.json"
FORMAT_VERSION = 1

type MissionKey = tuple[str, str]  # (server UID, mission UID)


@dataclass(frozen=True, slots=True)
class TourMark:
    started_at: datetime
    ended_at: datetime | None
    title: str
    mode: str
    by_win: bool


@dataclass(frozen=True, slots=True)
class AdminMarks:
    """The admin's settings that live on ingested rows, by their stable keys."""

    hidden_players: frozenset[str]  # account UUIDs
    hidden_missions: frozenset[MissionKey]
    tours: tuple[TourMark, ...]
    runs: tuple[
        tuple[int, str, str], ...
    ] = ()  # (IngestRun pk, server UID, mission UID) of the runs that had a mission


@dataclass(slots=True)
class WipeState:
    marks: AdminMarks
    players: dict[str, int] = field(default_factory=dict[str, int])  # account UUID -> pk
    missions: dict[MissionKey, int] = field(default_factory=dict[MissionKey, int])
    sorties: dict[MissionKey, dict[tuple[str, int], int]] = field(
        default_factory=dict[MissionKey, dict[tuple[str, int], int]]
    )  # mission -> (account UUID, spawn tick) -> pk
    tours: dict[datetime, int] = field(default_factory=dict[datetime, int])  # a tour's start -> pk


# --- capture ---


def capture_state() -> WipeState:
    """Everything the wipe is about to delete that has to come back, read from the database now."""
    marks = AdminMarks(
        hidden_players=frozenset(Player.objects.hidden().values_list("account_uuid", flat=True)),
        hidden_missions=frozenset(
            (str(server), uid) for server, uid in Mission.objects.hidden().values_list("server_uid", "mission_uid")
        ),
        tours=tuple(
            TourMark(t.started_at, t.ended_at, t.title, t.mode, t.by_win) for t in Tour.objects.order_by("started_at")
        ),
        runs=tuple(
            (pk, str(server), uid)
            for pk, server, uid in IngestRun.objects.filter(mission__isnull=False).values_list(
                "pk", "mission__server_uid", "mission__mission_uid"
            )
        ),
    )
    state = WipeState(marks)
    state.players = {account: pk for account, pk in Player.objects.values_list("account_uuid", "pk")}
    state.missions = {
        (str(server), uid): pk for pk, server, uid in Mission.objects.values_list("pk", "server_uid", "mission_uid")
    }
    state.tours = {started: pk for started, pk in Tour.objects.values_list("started_at", "pk")}
    rows = PlayerSortie.objects.values_list(
        "pk", "mission__server_uid", "mission__mission_uid", "account_uuid", "spawn_tick"
    )
    for pk, server, uid, account, tick in rows.iterator(chunk_size=5000):
        state.sorties.setdefault((str(server), uid), {})[(account, tick)] = pk
    return state


def merge(old: WipeState, new: WipeState) -> WipeState:
    """A wipe started while an earlier one never finished: nothing the earlier one saved may be lost, and what is in the
    database now (rows that came back) is the newer truth for the keys."""
    # Rows that came back are in `new` and the database is their truth: an admin who unhid a player or renamed a tour
    # after the interruption must not be reverted. What is not back yet (`old` only) is still waiting for its row.
    tours = {_tour_key(t): t for t in old.marks.tours}
    tours.update({_tour_key(t): t for t in new.marks.tours})
    marks = AdminMarks(
        hidden_players=frozenset(a for a in old.marks.hidden_players if a not in new.players)
        | new.marks.hidden_players,
        hidden_missions=frozenset(m for m in old.marks.hidden_missions if m not in new.missions)
        | new.marks.hidden_missions,
        tours=tuple(sorted(tours.values(), key=lambda t: t.started_at)),
        runs=tuple({run[0]: run for run in (*old.marks.runs, *new.marks.runs)}.values()),
    )
    merged = WipeState(marks, {**old.players, **new.players}, {**old.missions, **new.missions})
    merged.tours = {**old.tours, **new.tours}
    for key in {*old.sorties, *new.sorties}:
        merged.sorties[key] = {**old.sorties.get(key, {}), **new.sorties.get(key, {})}
    return merged


def _tour_key(mark: TourMark) -> tuple[datetime, str, bool]:
    return (mark.started_at, mark.mode, mark.by_win)


# --- the file ---


def state_path(cfg: Config) -> Path:
    return cfg.data_dir / STATE_NAME


def pending(cfg: Config) -> bool:
    return state_path(cfg).exists()


def save(cfg: Config, state: WipeState) -> None:
    """Write the file durably (flushed, then renamed over the old one): a crash right after this call finds it."""
    document: dict[str, object] = {
        "version": FORMAT_VERSION,
        "hidden_players": sorted(state.marks.hidden_players),
        "hidden_missions": sorted(state.marks.hidden_missions),
        "tours": [
            [t.started_at.isoformat(), t.ended_at.isoformat() if t.ended_at else None, t.title, t.mode, t.by_win]
            for t in state.marks.tours
        ],
        "runs": [list(run) for run in state.marks.runs],
        "players": state.players,
        "missions": [[server, uid, pk] for (server, uid), pk in state.missions.items()],
        "tour_pks": [[started.isoformat(), pk] for started, pk in state.tours.items()],
        "sorties": [
            [server, uid, account, tick, pk]
            for (server, uid), by_key in state.sorties.items()
            for (account, tick), pk in by_key.items()
        ],
    }
    path = state_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(document, handle, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def load(cfg: Config) -> WipeState | None:
    """The saved state, or None when there is none (or the file is unreadable: logged, not resumable)."""
    path = state_path(cfg)
    if not path.exists():
        return None
    try:
        doc = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
        if doc.get("version") != FORMAT_VERSION:
            raise ValueError(f"unknown format version {doc.get('version')!r}")
        marks = AdminMarks(
            hidden_players=frozenset(doc["hidden_players"]),
            hidden_missions=frozenset((s, u) for s, u in doc["hidden_missions"]),
            tours=tuple(
                TourMark(
                    datetime.fromisoformat(started),
                    datetime.fromisoformat(ended) if ended else None,
                    title,
                    mode,
                    by_win,
                )
                for started, ended, title, mode, by_win in doc["tours"]
            ),
            runs=tuple((pk, s, u) for pk, s, u in doc["runs"]),
        )
        state = WipeState(marks, dict(doc["players"]), {(s, u): pk for s, u, pk in doc["missions"]})
        state.tours = {datetime.fromisoformat(started): pk for started, pk in doc["tour_pks"]}
        for server, uid, account, tick, pk in doc["sorties"]:
            state.sorties.setdefault((server, uid), {})[(account, tick)] = pk
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.error("%s is unreadable (%s): the marks of the interrupted wipe cannot be applied", path, exc)
        return None
    return state


def clear(cfg: Config) -> None:
    state_path(cfg).unlink(missing_ok=True)
    deactivate()


# --- the active state: what the receivers and persist consult ---

_active: WipeState | None = None
_active_dir: Path | None = None  # the data dir the active state belongs to
_receivers: list[tuple[type[models.Model], Any]] = []


def active() -> WipeState | None:
    return _active


def activate(state: WipeState, data_dir: Path) -> None:
    global _active, _active_dir
    _active, _active_dir = state, data_dir
    if not _receivers:
        for model, receiver in ((Player, _new_player), (Mission, _new_mission), (Tour, _new_tour)):
            pre_save.connect(receiver, sender=model, weak=False, dispatch_uid=f"wipe-state-{model.__name__}")
            _receivers.append((model, receiver))


def deactivate() -> None:
    global _active, _active_dir
    _active = _active_dir = None
    for model, receiver in _receivers:
        pre_save.disconnect(receiver, sender=model, dispatch_uid=f"wipe-state-{model.__name__}")
    _receivers.clear()


def resume(cfg: Config) -> WipeState | None:
    """Activate the saved state of a wipe that never finished (once per process; later calls return the active one)."""
    if _active is not None:
        if _active_dir == cfg.data_dir and pending(cfg):
            return _active
        deactivate()  # another data dir (a test, a second install), or the file is gone (another process finished it)
    state = load(cfg)
    if state is not None:
        log.warning("a wipe was interrupted: its saved marks and keys are applied to the rows that come back")
        activate(state, cfg.data_dir)
    return state


def unfinished_missions(state: WipeState) -> int:
    """How many of the missions the wipe deleted have not come back yet."""
    present = {(str(server), uid) for server, uid in Mission.objects.values_list("server_uid", "mission_uid")}
    return len(set(state.missions) - present)


def _free(model: type[models.Model], pk: int | None) -> bool:
    return pk is not None and not model._default_manager.filter(pk=pk).exists()


def _new_player(sender: type[models.Model], instance: models.Model, **_kwargs: object) -> None:
    state = _active
    if state is None or not instance._state.adding or not isinstance(instance, Player):
        return
    old = state.players.get(instance.account_uuid)
    if instance.pk is None and old is not None and _free(Player, old):
        instance.pk = old
    if instance.account_uuid in state.marks.hidden_players:
        instance.is_hidden = True


def _new_mission(sender: type[models.Model], instance: models.Model, **_kwargs: object) -> None:
    state = _active
    if state is None or not instance._state.adding or not isinstance(instance, Mission):
        return
    key = (str(instance.server_uid), instance.mission_uid)
    old = state.missions.get(key)
    if instance.pk is None and old is not None and _free(Mission, old):
        instance.pk = old
    if key in state.marks.hidden_missions:
        instance.is_hidden = True


def _new_tour(sender: type[models.Model], instance: models.Model, **_kwargs: object) -> None:
    state = _active
    if state is None or not instance._state.adding or not isinstance(instance, Tour):
        return
    old = state.tours.get(instance.started_at)
    if instance.pk is None and old is not None and _free(Tour, old):
        instance.pk = old
    for mark in state.marks.tours:  # the admin's name, given once at creation: a later rename is never reverted
        if _tour_key(mark) == (instance.started_at, instance.mode, instance.by_win):
            instance.title = mark.title


def reserved_sortie_pks(
    server_uid: uuid.UUID, mission_uid: str, keys: Iterable[tuple[str, int]]
) -> dict[tuple[str, int], int]:
    """The old pks for the sorties about to be inserted (key = account UUID, spawn tick); only free ones are given."""
    state = _active
    if state is None:
        return {}
    by_key = state.sorties.get((str(server_uid), mission_uid))
    if not by_key:
        return {}
    wanted = {key: by_key[key] for key in keys if key in by_key}
    if not wanted:
        return {}
    taken = set(PlayerSortie.objects.filter(pk__in=list(wanted.values())).values_list("pk", flat=True))
    return {key: pk for key, pk in wanted.items() if pk not in taken}
