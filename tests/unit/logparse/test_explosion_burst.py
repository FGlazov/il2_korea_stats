"""Explosion bursts (speed round 3): `parse_lines` merges consecutive `AMMO:explosion` hit lines of one tick and
attacker into one `ExplosionBurstEvent`. The result must be exactly what per-line parsing gives: the same events once
expanded, the same stats and warnings, the same replay. The reference is `parse_lines(..., _coalesce=False)` (the same
loop without bursts, whose lines go through `_parse`) and the generic tokenizer (`_parse(fast=False)`)."""

import random
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path

import pytest

from il2ks.core.catalog.loader import load_default_catalog
from il2ks.core.logparse.events import ExplosionBurstEvent, HitEvent, LogEvent, ObjectId
from il2ks.core.logparse.parser import ParseStats, _parse, parse_lines  # pyright: ignore[reportPrivateUsage]
from il2ks.core.replay.state import Replay
from tests.conftest import FIXTURE_LOGS
from tests.unit.logparse.test_fast_path import fixture_lines, mutate

FIXTURES = sorted(FIXTURE_LOGS.glob("*.txt.zip"))


def expand(events: Iterable[LogEvent]) -> list[LogEvent]:
    """Bursts back to one HitEvent per line."""
    out: list[LogEvent] = []
    for event in events:
        if isinstance(event, ExplosionBurstEvent):
            out.extend(
                HitEvent(tick=event.tick, ammo="explosion", attacker_id=event.attacker_id, target_id=t)
                for t in event.target_ids
            )
        else:
            out.append(event)
    return out


def run(lines: list[str], *, coalesce: bool) -> tuple[list[LogEvent], ParseStats]:
    stats = ParseStats()
    return list(parse_lines(lines, stats, _coalesce=coalesce)), stats


def check(lines: list[str]) -> list[LogEvent]:
    """Bursts vs per-line: same events (expanded), same counters and warnings. Returns the burst events."""
    merged, merged_stats = run(lines, coalesce=True)
    plain, plain_stats = run(lines, coalesce=False)
    assert expand(merged) == plain
    assert asdict(merged_stats) == asdict(plain_stats)
    return merged


@pytest.mark.parametrize("path", FIXTURES, ids=[p.name for p in FIXTURES])
def test_bursts_equal_per_line_parsing_on_fixtures(path: Path) -> None:
    lines = fixture_lines(path)
    merged = check(lines)
    assert not any(isinstance(e, HitEvent) and e.ammo == "explosion" for e in merged)


@pytest.mark.parametrize("path", FIXTURES, ids=[p.name for p in FIXTURES])
def test_expanded_bursts_equal_the_generic_tokenizer(path: Path) -> None:
    lines = [line for line in fixture_lines(path) if "AMMO:explosion" in line]
    reference = [_parse(line, fast=False)[1] for line in lines]
    assert expand(run(lines, coalesce=True)[0]) == reference


@pytest.mark.parametrize("path", FIXTURES, ids=[p.name for p in FIXTURES])
def test_bursts_equal_per_line_parsing_on_mutated_lines(path: Path) -> None:
    rng = random.Random(20261005)
    lines = fixture_lines(path)
    # explosion-heavy slices, so mutations land inside and between bursts
    for _ in range(40):
        start = rng.randrange(len(lines))
        chunk = lines[start : start + 400]
        chunk = [mutate(line, rng) if rng.random() < 0.05 else line for line in chunk]
        if rng.random() < 0.3:
            chunk.insert(rng.randrange(len(chunk) + 1), rng.choice(["", "   ", "garbage"]))
        check(chunk)


@pytest.mark.parametrize("path", FIXTURES, ids=[p.name for p in FIXTURES])
def test_batches_give_the_same_events_as_one_pass(path: Path) -> None:
    """Live mode: a burst that spans two batches comes out as two bursts, the same lines in the same order."""
    rng = random.Random(7)
    lines = fixture_lines(path)[:20000]
    expected = run(lines, coalesce=False)[0]
    events: list[LogEvent] = []
    total = 0
    at = 0
    while at < len(lines):
        size = rng.choice([1, 2, 3, 50, 777])
        stats = ParseStats()
        events.extend(parse_lines(lines[at : at + size], stats))
        total += stats.lines_total
        at += size
    assert expand(events) == expected
    assert total == len(lines)


def line(tick: int | str, attacker: int | str, target: int | str) -> str:
    return f"T:{tick} AType:1 AMMO:explosion AID:{attacker} TID:{target}"


def test_consecutive_lines_of_one_tick_and_attacker_are_one_burst() -> None:
    events = check([line(5, 1, 2), line(5, 1, 3), line(5, 1, 2), line(6, 1, 2), line(6, 9, 2), line(6, 9, 4)])
    assert events == [
        ExplosionBurstEvent(tick=5, attacker_id=ObjectId(1), target_ids=(ObjectId(2), ObjectId(3), ObjectId(2))),
        ExplosionBurstEvent(tick=6, attacker_id=ObjectId(1), target_ids=(ObjectId(2),)),
        ExplosionBurstEvent(tick=6, attacker_id=ObjectId(9), target_ids=(ObjectId(2), ObjectId(4))),
    ]


def test_another_event_between_lines_ends_the_burst() -> None:
    events = check([line(5, 1, 2), "T:5 AType:2 DMG:0.5 AID:1 TID:2 POS(1,2,3)", line(5, 1, 3)])
    assert [type(e).__name__ for e in events] == ["ExplosionBurstEvent", "DamageEvent", "ExplosionBurstEvent"]


@pytest.mark.parametrize(
    "odd",
    [
        line(5, 1, 3) + " ",  # trailing space
        line(5, 1, 3) + " EXTRA:1",
        " " + line(5, 1, 3),
        line(5, 1, 3).replace("TID", " TID"),  # two spaces
        line(5, 1, "+3"),
        line(5, 1, "-1"),
        line(5, "-1", 3),
        line(5, 1, ""),
        line(5, 1, "3x"),
        line(5, 1, "٣"),  # Arabic-indic digit: int() reads it, the burst shape does not
        line("x", 1, 3),
        line(5, 1, 3).replace("explosion", "Explosion"),
        line(5, 1, 3).replace("AMMO:explosion ", ""),
        line(5, 1, 3).replace(" TID:3", ""),
        line(5, 1, 3).replace(" AID:1", " TID:3 AID:1"),
        line(5, 1, 3).replace("AID:1 TID:3", "TID:3 AID:1"),
        line(5, 1, 3) + "\t",
        line(5, 1, 3) + "\r",
        line(5, 1, "3 4"),
        line("5 ", 1, 3),
        line(" 5", 1, 3),
    ],
)
def test_a_line_of_another_shape_takes_the_normal_path_and_ends_the_burst(odd: str) -> None:
    check([line(5, 1, 2), odd, line(5, 1, 4)])
    check([odd, line(5, 1, 2), line(5, 1, 3)])
    check([line(5, 1, 2), line(5, 1, 3), odd])


def test_a_bad_line_inside_a_burst_is_counted_and_numbered_like_before() -> None:
    lines = [line(5, 1, 2), "T:5 AType:1 AMMO:explosion AID:1", line(5, 1, 3), "", line(5, 1, 4)]
    events = check(lines)
    assert len(events) == 2  # the blank line is skipped and does not end the second burst
    stats = run(lines, coalesce=True)[1]
    assert stats.lines_total == 4
    assert stats.lines_bad == 1
    assert stats.warnings[0].startswith("line 2: missing key TID")


def test_a_very_long_tick_still_parses() -> None:
    check([line("1" * 30, 1, 2), line("1" * 30, 1, 3)])


@pytest.mark.parametrize("path", FIXTURES, ids=[p.name for p in FIXTURES])
def test_replay_is_the_same_with_bursts(path: Path) -> None:
    lines = fixture_lines(path)
    catalog = load_default_catalog()
    results: list[object] = []
    for coalesce in (True, False):
        replay = Replay(catalog)
        for event in run(lines, coalesce=coalesce)[0]:
            replay.feed(event)
        detonations = {
            oid: [(d.tick, list(d.targets)) for d in obj.detonations]
            for oid, obj in replay._objects.items()  # pyright: ignore[reportPrivateUsage]
            if obj.detonations
        }
        results.append((replay.finish(), detonations))
    assert results[0] == results[1]


def test_the_fixtures_have_real_bursts() -> None:
    sizes = [
        len(e.target_ids)
        for path in FIXTURES
        for e in run(fixture_lines(path), coalesce=True)[0]
        if isinstance(e, ExplosionBurstEvent)
    ]
    assert max(sizes) > 1
