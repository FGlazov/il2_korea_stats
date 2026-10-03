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
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from django.db import transaction
from django.db.models import F, Q

from il2ks.core.tours import TourRules, period_for
from il2ks.db.models import Mission, Tour

CHUNK = 500  # mission ids per UPDATE: far below SQLite's bound-parameter limit


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


def assign_missing(rules: TourRules) -> int:
    """Give a tour to missions that have none (saved before tours existed). Returns how many.

    Level 2 is the caller's."""
    ids_by_tour: defaultdict[int, list[int]] = defaultdict(list)
    for pk, started_at in (
        Mission.objects.filter(tour__isnull=True).order_by("started_at").values_list("pk", "started_at")
    ):
        ids_by_tour[ensure_tour(rules, started_at).pk].append(pk)
    total = 0
    for tour_id, ids in ids_by_tour.items():
        for start in range(0, len(ids), CHUNK):
            total += Mission.objects.filter(pk__in=ids[start : start + CHUNK]).update(tour_id=tour_id)
    return total


@dataclass(frozen=True, slots=True)
class RetourSummary:
    missions: int  # missions now in a tour
    tours: int  # tours after the reassignment


def retour(rules: TourRules) -> RetourSummary:
    """Reassign every mission to its tour under `rules` (after a mode, start or timezone change). Run in a transaction.

    The caller rebuilds the level-2 rows afterwards (`rebuild_aggregates`, which `rebuild-aggregates --retour` does)."""
    if rules.mode == "manual":
        _retour_manual(rules)
    else:
        _retour_calendar(rules)
        Tour.objects.filter(missions__isnull=True).delete()
    return RetourSummary(Mission.objects.filter(tour__isnull=False).count(), Tour.objects.count())


def _retour_calendar(rules: TourRules) -> None:
    """One UPDATE per non-empty period: periods are contiguous ranges of `started_at`, so no id lists are needed."""
    existing = {t.started_at: t for t in Tour.objects.all()}
    cursor = Mission.objects.order_by("started_at").values_list("started_at", flat=True).first()
    while cursor is not None:
        period = period_for(rules, cursor)
        tour = existing.get(period.started_at)
        if tour is None:
            tour = Tour.objects.create(
                title=period.title, started_at=period.started_at, ended_at=period.ended_at, mode=rules.label
            )
        else:
            if tour.ended_at != period.ended_at:
                tour.title = period.title  # other boundaries: the old title (a rename) no longer describes it
            tour.ended_at, tour.mode = period.ended_at, rules.label
            tour.save(update_fields=["title", "ended_at", "mode"])
        Mission.objects.filter(started_at__gte=period.started_at, started_at__lt=period.ended_at).update(tour=tour)
        cursor = (
            Mission.objects.filter(started_at__gte=period.ended_at)
            .order_by("started_at")
            .values_list("started_at", flat=True)
            .first()
        )


def _retour_manual(rules: TourRules) -> None:
    """Manual mode: the stored tours are the boundaries (see module doc). The newest one is (re)opened, the others end
    where the next one starts. With no tour yet, one open "Tour 1" starts at the first mission."""
    tours = list(Tour.objects.order_by("started_at"))
    if not tours:
        first = Mission.objects.order_by("started_at").values_list("started_at", flat=True).first()
        if first is None:
            return
        tours = [Tour.objects.create(title="Tour 1", started_at=first, ended_at=None, mode=rules.label)]
    for tour, following in zip(tours, [*tours[1:], None], strict=True):
        tour.ended_at = following.started_at if following is not None else None
        tour.mode = rules.label
        tour.save(update_fields=["ended_at", "mode"])
        rows = Mission.objects.filter(started_at__lt=tour.ended_at) if tour.ended_at else Mission.objects.all()
        if tour is not tours[0]:
            rows = rows.filter(started_at__gte=tour.started_at)  # the first tour also holds everything older
        rows.update(tour=tour)


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
    if rules.mode == "manual":
        return False
    period = period_for(rules, tour.started_at)
    return (period.started_at, period.ended_at) != (tour.started_at, tour.ended_at)


def tour_problems(rules: TourRules) -> TourProblems:
    outside = Mission.objects.filter(tour__isnull=False).filter(
        Q(started_at__lt=F("tour__started_at")) | Q(tour__ended_at__isnull=False, started_at__gte=F("tour__ended_at"))
    )
    return TourProblems(
        missions_without_tour=Mission.objects.filter(tour__isnull=True).count(),
        stale_tours=sum(1 for tour in Tour.objects.all() if _is_stale(rules, tour)),
        missions_outside_their_tour=0 if rules.mode == "manual" else outside.count(),
    )
