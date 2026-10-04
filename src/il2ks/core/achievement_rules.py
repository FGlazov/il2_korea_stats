"""Admin choices that change how achievements are *computed* (FR-WEB-26, doc 17): which ones are off and the tier
thresholds. Pure data and validation; the storage (a JSON on `SiteSettings`) and the words live in the outer layers.

`Rules` holds only what differs from the built-in registry, so `Rules()` is "defaults" and two `Rules` are equal exactly
when they compute the same rows. The rows in the database were computed with the *applied* rules; when the admin's
wanted rules differ, a recompute is pending (`ingest.achievements.recompute_pending`).

Threshold rule: as many tiers as the built-in achievement has (the tier names and ribbon stripes are four fixed slots),
positive whole numbers, strictly increasing, at most `MAX_THRESHOLD`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from itertools import pairwise
from typing import Final, Literal, cast

from il2ks.core.achievements import ACHIEVEMENTS, BY_KEY, Achievement

MAX_THRESHOLD: Final = 1_000_000

type ThresholdProblem = Literal["count", "positive", "increasing", "large"]


def threshold_problem(achievement: Achievement, values: Sequence[int]) -> ThresholdProblem | None:
    """Why `values` cannot be the tier thresholds of `achievement`, None when they can."""
    if len(values) != achievement.top_tier:
        return "count"
    if any(v < 1 for v in values):
        return "positive"
    if any(b <= a for a, b in pairwise(values)):
        return "increasing"
    if any(v > MAX_THRESHOLD for v in values):
        return "large"
    return None


@dataclass(frozen=True, slots=True)
class Rules:
    off: frozenset[str] = frozenset()
    thresholds: Mapping[str, tuple[int, ...]] = field(default_factory=lambda: {})

    @staticmethod
    def from_json(raw: object) -> "Rules":
        """Tolerant parse of `{"off": [key, ...], "thresholds": {key: [n, ...]}}`: unknown keys, invalid thresholds and
        thresholds equal to the built-in ones are dropped, so a hand-edited row never breaks a page."""
        data: Mapping[str, object] = cast(Mapping[str, object], raw) if isinstance(raw, dict) else {}
        raw_off: object = data.get("off")
        off = (
            frozenset(k for k in cast(list[object], raw_off) if isinstance(k, str) and k in BY_KEY)
            if isinstance(raw_off, list)
            else frozenset[str]()
        )
        thresholds: dict[str, tuple[int, ...]] = {}
        raw_thr: object = data.get("thresholds")
        if isinstance(raw_thr, dict):
            for key, values in cast(Mapping[object, object], raw_thr).items():
                if not (isinstance(key, str) and key in BY_KEY and isinstance(values, list)):
                    continue
                numbers = cast(list[object], values)
                if not all(isinstance(v, int) and not isinstance(v, bool) for v in numbers):
                    continue
                wanted = tuple(cast(list[int], numbers))
                if threshold_problem(BY_KEY[key], wanted) is None and wanted != BY_KEY[key].thresholds:
                    thresholds[key] = wanted
        return Rules(off, thresholds)

    def to_json(self) -> dict[str, object]:
        return {"off": sorted(self.off), "thresholds": {k: list(v) for k, v in sorted(self.thresholds.items())}}

    def enabled(self, key: str) -> bool:
        return key in BY_KEY and key not in self.off

    def achievement(self, key: str) -> Achievement | None:
        """The achievement with its effective thresholds; None when unknown or switched off."""
        base = BY_KEY.get(key)
        if base is None or key in self.off:
            return None
        thresholds = self.thresholds.get(key)
        return replace(base, thresholds=thresholds) if thresholds else base

    def active(self) -> tuple[Achievement, ...]:
        """Every switched-on achievement in registry order, with its effective thresholds."""
        return tuple(a for base in ACHIEVEMENTS if (a := self.achievement(base.key)) is not None)
