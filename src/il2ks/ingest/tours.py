"""Tour assignment (TD-26, FR-WEB-10): which `Tour` a mission belongs to, and reassigning after a mode change.

A mission belongs to the tour that contains its `started_at` (UTC instant; boundaries are local midnights in
`[tours] timezone`, see `core.tours`). `ensure_tour` is what `save_mission` calls: it finds or creates that tour.

- `monthly` / `days:<N>`: the period comes from the calendar; the `Tour` row is created on the first mission in it.
- `manual`: the stored tours are the boundaries. The tour with the greatest `started_at` not after the mission's start
  holds it (a mission older than every tour goes to the first one). With no tour at all, an open "Tour 1" is created at
  the mission's start (created on first use). Only an admin starts the next one (`start_manual_tour`, FR-ADM-8).

`retour` reassigns every mission after the mode, start date or timezone changed (`rebuild-aggregates --retour`); the
caller then rebuilds the level-2 rows. It keeps a tour row (and so its admin-edited title and its PK) whose boundaries
are unchanged, and deletes tours that end up without missions (not in manual mode: an empty open tour is wanted there).
Level-2 rows per tour are not touched here: `ingest.aggregates` recomputes them from `PlayerMission` and `Mission.tour`.

Decisive missions (the admin option "start a new tour when a mission is won by one side", `SiteSettings.tour_on_win`,
`TourRules.on_win`) [PROPOSED] cut the period the mode draws (a calendar period, or the stretch between two manual
boundaries) into parts: every mission won by one side (`Mission.result == "win"`; an old row with a winner but no result
is not decisive until `il2ks reprocess` reads it) ends its part at its own end
(`core.tours.win_cuts`), and the next mission starts a new `Tour` with `by_win` set. Which tour holds a mission is still
a function of the missions alone: `resegment` rebuilds the parts of one period, `save_level1` calls it for the periods a
saved mission touches and `retour` for every period, so incremental ingest (in any order), re-ingest and
`rebuild-aggregates --retour` end up with the same tours. A part without a mission has no row.

The option is a pair like the achievements': what the admin chose (`tour_on_win`) and what the stored tours were
assigned with (`tour_on_win_applied`). Ingest follows the applied value; a retour adopts the chosen one.
"""

import re
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from django.db import transaction
from django.db.models import F, Q, QuerySet

from il2ks.core.tours import MissionSpan, TourRules, period_for, segment_title, win_cuts
from il2ks.db.models import Mission, PlayerSortie, SiteSettings, Tour

CHUNK = 500  # mission ids per UPDATE: far below SQLite's bound-parameter limit


def wanted_on_win() -> bool:
    """The admin's choice: start a new tour after a mission won by one side."""
    return bool(SiteSettings.objects.filter(pk=1).values_list("tour_on_win", flat=True).first())


def applied_on_win() -> bool:
    """What the stored tours were assigned with (the rule ingest follows until the next retour)."""
    return bool(SiteSettings.objects.filter(pk=1).values_list("tour_on_win_applied", flat=True).first())


def on_win_pending() -> bool:
    """The admin changed the option since the tours were last assigned: a retour (and level-2 rebuild) is due."""
    return wanted_on_win() != applied_on_win()


def effective_rules(rules: TourRules) -> TourRules:
    """The `[tours]` rules with the option as the stored tours have it."""
    return replace(rules, on_win=applied_on_win())


def ensure_tour(rules: TourRules, started_at: datetime) -> Tour:
    """The tour containing `started_at` (aware), created when it doesn't exist yet."""
    if rules.mode == "manual":
        return _manual_tour_for(rules, started_at)
    period = period_for(rules, started_at)
    tour, _ = Tour.objects.get_or_create(
        started_at=period.started_at,
        defaults={"title": period.title, "ended_at": period.ended_at, "mode": rules.label},
    )
    return tour


def _manual_tour_for(rules: TourRules, started_at: datetime) -> Tour:
    tour = Tour.objects.filter(started_at__lte=started_at).order_by("-started_at").first()
    if tour is None:
        tour = Tour.objects.order_by("started_at").first()  # older than every tour: the first one holds it
    if tour is None:
        tour = Tour.objects.create(title="Tour 1", started_at=started_at, ended_at=None, mode=rules.label)
    return tour


def _next_tour_number() -> int:
    """One more than the highest "Tour N" title in use (not the row count: deleting a tour must not repeat a title)."""
    numbers = [
        int(match.group(1))
        for title in Tour.objects.values_list("title", flat=True)
        if (match := re.fullmatch(r"Tour (\d+)", title))
    ]
    return max(numbers, default=0) + 1


def start_manual_tour(now: datetime | None = None, title: str = "") -> Tour:
    """FR-ADM-8: close the open tour at `now` and open a new one. Missions that started earlier stay in the old one.

    Raises ValueError when `now` is not after the open tour's start (a tour must last at least an instant)."""
    moment = now or datetime.now(UTC)
    with transaction.atomic():
        current = Tour.objects.filter(ended_at__isnull=True).order_by("-started_at").first()
        if current is not None:
            if moment <= current.started_at:
                raise ValueError("the open tour started at or after this moment")
            current.ended_at = moment
            current.save(update_fields=["ended_at"])
        number = _next_tour_number()
        return Tour.objects.create(title=title or f"Tour {number}", started_at=moment, ended_at=None, mode="manual")


def move_missions(mission_ids: Iterable[int], tour_id: int) -> int:
    """Put missions into a tour and their sorties with them: the only writer of `Mission.tour` besides a mission's own
    save (`PlayerSortie.tour` is always the mission's, doc 14 "The sortie's tour"). Returns the missions changed."""
    ids = list(mission_ids)
    total = 0
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start : start + CHUNK]
        total += Mission.objects.filter(pk__in=chunk).update(tour_id=tour_id)
        PlayerSortie.objects.filter(mission_id__in=chunk).update(tour_id=tour_id)
    return total


def assign_missing(rules: TourRules) -> int:
    """Give a tour to missions that have none (saved before tours existed). Returns how many.

    Level 2 is the caller's."""
    rules = effective_rules(rules)
    if rules.on_win and Mission.objects.filter(tour__isnull=True).exists():
        missing = Mission.objects.filter(tour__isnull=True).count()
        _retour_all(rules)  # the parts a decisive mission cuts depend on the other missions: assign them all
        return missing
    ids_by_tour: defaultdict[int, list[int]] = defaultdict(list)
    for pk, started_at in (
        Mission.objects.filter(tour__isnull=True).order_by("started_at").values_list("pk", "started_at")
    ):
        ids_by_tour[ensure_tour(rules, started_at).pk].append(pk)
    return sum(move_missions(ids, tour_id) for tour_id, ids in ids_by_tour.items())


@dataclass(frozen=True, slots=True)
class RetourSummary:
    missions: int  # missions now in a tour
    tours: int  # tours after the reassignment


def retour(rules: TourRules) -> RetourSummary:
    """Reassign every mission to its tour under `rules` (after a mode, start or timezone change). Run in a transaction.

    The option "new tour after a decisive mission" is adopted first: the chosen value becomes the applied one, so the
    parts follow what the admin ticked. The caller rebuilds the level-2 rows afterwards (`rebuild_aggregates`, which
    `rebuild-aggregates --retour` does)."""
    SiteSettings.objects.filter(pk=1).update(tour_on_win_applied=F("tour_on_win"))
    _retour_all(effective_rules(rules))
    return RetourSummary(Mission.objects.filter(tour__isnull=False).count(), Tour.objects.count())


def _retour_all(rules: TourRules) -> None:
    if rules.mode == "manual":
        _retour_manual(rules)
    else:
        _retour_calendar(rules)
        Tour.objects.filter(missions__isnull=True).delete()


@dataclass(frozen=True, slots=True)
class _Stretch:
    """A period the mode draws, which decisive missions may cut into parts: `[start, end)` (None = unbounded), the
    title of its first part, and the first part's tour row when it exists."""

    start: datetime | None
    end: datetime | None
    title: str
    base: Tour | None
    keep_empty_base: bool = False  # manual mode: the admin's boundary stays even while its first part is empty


def _missions_between(start: datetime | None, end: datetime | None) -> QuerySet[Mission]:
    rows = Mission.objects.all()
    if start is not None:
        rows = rows.filter(started_at__gte=start)
    if end is not None:
        rows = rows.filter(started_at__lt=end)
    return rows


def _decisive_spans(start: datetime | None, end: datetime | None) -> list[MissionSpan]:
    """The decisive missions whose end could cut `[start, end)`: found by their end, which may lie in a later period
    than their start."""
    rows = Mission.objects.filter(result="win")  # not `winning_coalition`: old rows kept a winner without a result
    if start is not None:
        rows = rows.filter(ended_at__gt=start)
    if end is not None:
        rows = rows.filter(ended_at__lt=end)
    return [MissionSpan(s, e, True) for s, e in rows.values_list("started_at", "ended_at")]


def _is_automatic_part_title(base_title: str, title: str) -> bool:
    return re.fullmatch(re.escape(base_title) + r" \(\d+\)", title) is not None


def resegment(rules: TourRules, stretch: _Stretch) -> set[int]:
    """Assign the missions of one period to its parts under `rules`: the part boundaries are `win_cuts` when
    `rules.on_win`, else the period is one tour. Creates, retimes and deletes the `by_win` tours of the period, moves
    the missions; a part without a mission has no tour (an empty first part keeps its row in manual mode only).
    Returns the ids of the tours whose missions changed (the caller refreshes level 2 for them), not deleted ones."""
    cuts = win_cuts(stretch.start, stretch.end, _decisive_spans(stretch.start, stretch.end)) if rules.on_win else ()
    bounds = [stretch.start, *cuts, stretch.end]
    touched: set[int] = set()
    keep: set[int] = set()
    emptied: Tour | None = None
    for index, (low, high) in enumerate(pairwise(bounds)):
        rows = _missions_between(low, high)
        has_missions = rows.exists()
        tour: Tour | None
        if index == 0:
            tour = stretch.base
            if tour is None and has_missions and low is not None:
                tour = Tour.objects.create(
                    title=stretch.title, started_at=low, ended_at=high, mode=rules.label, by_win=False
                )
            elif tour is not None and not has_missions and not stretch.keep_empty_base:
                emptied, tour = tour, None  # a calendar period whose first part has no mission has no tour of its own
            elif tour is not None:
                tour.ended_at, tour.mode, tour.by_win = high, rules.label, False
                tour.save(update_fields=["ended_at", "mode", "by_win"])
        elif has_missions and low is not None:
            title = segment_title(stretch.title, index)
            tour = Tour.objects.filter(started_at=low).first()
            if tour is None:
                tour = Tour.objects.create(title=title, started_at=low, ended_at=high, mode=rules.label, by_win=True)
            else:
                if not tour.by_win or _is_automatic_part_title(stretch.title, tour.title):
                    tour.title = title  # an admin's rename of a part stays, the numbering of an automatic one follows
                tour.ended_at, tour.mode, tour.by_win = high, rules.label, True
                tour.save(update_fields=["title", "ended_at", "mode", "by_win"])
        else:
            tour = None
        if tour is None:
            continue
        keep.add(tour.pk)
        if has_missions:
            moved = rows.exclude(tour=tour)
            touched |= {t for t in moved.values_list("tour_id", flat=True).distinct() if t is not None}
            if move_missions(list(moved.values_list("pk", flat=True)), tour.pk):
                touched.add(tour.pk)
    parts = Tour.objects.filter(by_win=True)
    if stretch.start is not None:
        parts = parts.filter(started_at__gt=stretch.start)
    if stretch.end is not None:
        parts = parts.filter(started_at__lt=stretch.end)
    stale = parts.exclude(pk__in=keep)
    removed = dict(stale.values_list("pk", "started_at"))
    stale.delete()  # their missions were moved above
    if emptied is not None and not Mission.objects.filter(tour=emptied).exists():
        removed[emptied.pk] = emptied.started_at
        emptied.delete()
    return (touched - removed.keys()) | neighbours_of_removed(removed.values())


def neighbours_of_removed(started_ats: Iterable[datetime]) -> set[int]:
    """The tours right before and after each removed tour's start. A removed tour closes a gap in the tour order: the
    players of both sides may now have a longer run (Old Hand) and, when the newest tour went, the new newest tour is
    the one the current streaks live in, so the caller refreshes these tours as well."""
    found: set[int] = set()
    for started_at in started_ats:
        before = Tour.objects.filter(started_at__lt=started_at).order_by("-started_at").values_list("pk", flat=True)
        after = Tour.objects.filter(started_at__gt=started_at).order_by("started_at").values_list("pk", flat=True)
        found |= set(before[:1]) | set(after[:1])
    return found


def delete_empty_tours(tour_ids: Iterable[int], rules: TourRules) -> set[int]:
    """Delete these tours when no mission is left in them (not in manual mode: the admin's boundary stays) and return
    the surviving tours next to them (`neighbours_of_removed`), which the caller refreshes. A tour without a mission
    has no level-2 row of its own left after its refresh."""
    if rules.mode == "manual":
        return set()
    empty = list(Tour.objects.filter(pk__in=set(tour_ids), missions__isnull=True).values_list("pk", "started_at"))
    Tour.objects.filter(pk__in=[pk for pk, _ in empty]).delete()
    return neighbours_of_removed(started_at for _, started_at in empty)


def _calendar_stretch(rules: TourRules, instant: datetime) -> _Stretch:
    period = period_for(rules, instant)
    base = Tour.objects.filter(started_at=period.started_at).first()
    title = (
        base.title if base is not None else period.title
    )  # an admin's rename of the base titles the parts, as in a retour
    return _Stretch(period.started_at, period.ended_at, title, base)


def _manual_stretch(instant: datetime) -> _Stretch | None:
    """The stretch between two manual boundaries (the tours that are not `by_win`) holding `instant`; the first one
    holds everything older, the last one is open."""
    bases = list(Tour.objects.filter(by_win=False).order_by("started_at"))
    if not bases:
        return None
    index = max((i for i, t in enumerate(bases) if t.started_at <= instant), default=0)
    following = bases[index + 1].started_at if index + 1 < len(bases) else None
    base = bases[index]
    return _Stretch(None if index == 0 else base.started_at, following, base.title, base, keep_empty_base=True)


def resegment_around(rules: TourRules, instants: Iterable[datetime]) -> set[int]:
    """The incremental half: `resegment` the period of each instant (a saved mission's start and end, and the end it
    had before a re-ingest), each period once. Only meant for `rules.on_win`."""
    done: set[tuple[datetime | None, datetime | None]] = set()
    touched: set[int] = set()
    for instant in sorted(set(instants)):
        stretch = _manual_stretch(instant) if rules.mode == "manual" else _calendar_stretch(rules, instant)
        if stretch is None or (stretch.start, stretch.end) in done:
            continue
        done.add((stretch.start, stretch.end))
        touched |= resegment(rules, stretch)
    return touched


def _retour_calendar(rules: TourRules) -> None:
    """One `resegment` per non-empty period: periods are contiguous ranges of `started_at`, so no id lists are needed.
    A tour of the same start keeps its row (and its admin-edited title) when its boundaries are unchanged."""
    cursor = Mission.objects.order_by("started_at").values_list("started_at", flat=True).first()
    while cursor is not None:
        period = period_for(rules, cursor)
        existing = Tour.objects.filter(started_at=period.started_at).first()
        if existing is not None and existing.mode != rules.label:
            existing.title = period.title  # other rules: the old title (a rename) no longer describes it
            existing.save(update_fields=["title"])
        title = existing.title if existing is not None else period.title
        resegment(rules, _Stretch(period.started_at, period.ended_at, title, existing))
        cursor = (
            Mission.objects.filter(started_at__gte=period.ended_at)
            .order_by("started_at")
            .values_list("started_at", flat=True)
            .first()
        )


def _retour_manual(rules: TourRules) -> None:
    """Manual mode: the stored tours that are not `by_win` are the boundaries (see module doc). The newest one is
    (re)opened, the others end where the next one starts. With no tour yet, one open "Tour 1" starts at the first
    mission."""
    tours = list(Tour.objects.filter(by_win=False).order_by("started_at"))
    if not tours:
        first = Mission.objects.order_by("started_at").values_list("started_at", flat=True).first()
        if first is None:
            return
        tours = [Tour.objects.create(title="Tour 1", started_at=first, ended_at=None, mode=rules.label)]
    for index, tour in enumerate(tours):
        following = tours[index + 1].started_at if index + 1 < len(tours) else None
        stretch = _Stretch(None if index == 0 else tour.started_at, following, tour.title, tour, keep_empty_base=True)
        resegment(rules, stretch)


@dataclass(frozen=True, slots=True)
class OnWinProjection:
    """What the Tours page shows before an admin turns the option on."""

    tours: int  # about how many tours the missions so far would form (the stored tours' starts stand for the periods)
    recent_wins: int  # missions won by one side that ended in the last `days` days
    old_winners: int  # rows with a winner but no result (saved before results were read): not decisive until reprocess


def on_win_projection(now: datetime | None = None, days: int = 30) -> OnWinProjection:
    """The effect of the option, from three reads: the starts of the stored tours that are not parts (they stand for the
    calendar periods or the admin's boundaries), every mission's start, end and result, and the old winners. The parts
    a period would have are those of `core.tours.win_cuts`: a mission starting at or after a decisive end is in a later
    part."""
    since = (now or datetime.now(UTC)) - timedelta(days=days)
    bases = list(Tour.objects.filter(by_win=False).order_by("started_at").values_list("started_at", flat=True))
    missions = list(Mission.objects.order_by("started_at").values_list("started_at", "ended_at", "result"))
    win_ends = sorted({ended for started, ended, result in missions if result == "win" and ended > started})
    parts: set[tuple[int, int]] = set()  # (period, part) with a mission
    for started, _, _ in missions:
        period = max(bisect_right(bases, started) - 1, 0)
        low = bases[period] if period > 0 else None  # the first period holds everything older
        high = bases[period + 1] if period + 1 < len(bases) else None
        first = 0 if low is None else bisect_right(win_ends, low)
        last = len(win_ends) if high is None else bisect_left(win_ends, high)
        parts.add((period, bisect_right(win_ends[first:last], started)))
    recent = sum(1 for _, ended, result in missions if result == "win" and ended >= since)
    old = Mission.objects.filter(result="unknown", winning_coalition__isnull=False).count()
    return OnWinProjection(len(parts), recent, old)


@dataclass(frozen=True, slots=True)
class TourProblems:
    """What `il2ks doctor` reports: data that doesn't match the configured tour rules."""

    missions_without_tour: int
    stale_tours: int  # made under another mode, or (calendar modes) with boundaries the rules would not draw
    missions_outside_their_tour: int  # calendar modes only: started_at not inside [started_at, ended_at)

    @property
    def needs_retour(self) -> bool:
        return bool(self.missions_without_tour or self.stale_tours or self.missions_outside_their_tour)


def _is_stale(rules: TourRules, tour: Tour) -> bool:
    if tour.mode != rules.label:
        return True
    if tour.by_win and not rules.on_win:
        return True  # a part started by a decisive mission, but the option is off
    if rules.mode == "manual":
        return False
    period = period_for(rules, tour.started_at)
    if tour.by_win:
        return tour.ended_at is None or tour.ended_at > period.ended_at
    if tour.started_at != period.started_at:
        return True
    cut_short = rules.on_win and tour.ended_at is not None and tour.ended_at < period.ended_at
    return tour.ended_at != period.ended_at and not cut_short


def tour_problems(rules: TourRules) -> TourProblems:
    rules = effective_rules(rules)
    outside = Mission.objects.filter(tour__isnull=False).filter(
        Q(started_at__lt=F("tour__started_at")) | Q(tour__ended_at__isnull=False, started_at__gte=F("tour__ended_at"))
    )
    return TourProblems(
        missions_without_tour=Mission.objects.filter(tour__isnull=True).count(),
        stale_tours=sum(1 for tour in Tour.objects.all() if _is_stale(rules, tour)),
        missions_outside_their_tour=0 if rules.mode == "manual" else outside.count(),
    )
