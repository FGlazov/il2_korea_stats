"""The parser's regex fast path must give exactly what the generic tokenizer gives (TD-20): same event for good lines,
same error kind and message for bad ones. Real fixture lines, then deterministic mutations of them."""

import random
from pathlib import Path

import pytest

from il2ks.core.logparse.events import LogEvent
from il2ks.core.logparse.files import MissionLog, read_mission_lines
from il2ks.core.logparse.parser import ParseError, _parse  # pyright: ignore[reportPrivateUsage]
from tests.conftest import FIXTURE_LOGS

FIXTURES = sorted(FIXTURE_LOGS.glob("*.txt.zip"))

type Outcome = tuple[int, LogEvent] | tuple[str, str]


def outcome(line: str, *, fast: bool) -> Outcome:
    try:
        return _parse(line, fast=fast)
    except ParseError as e:
        return (e.kind.value, str(e))


def check(line: str) -> None:
    assert outcome(line, fast=True) == outcome(line, fast=False), line


def fixture_lines(path: Path) -> list[str]:
    return [line for line in read_mission_lines(MissionLog("fixture", "archive", (path,))) if line.strip()]


def mutate(line: str, rng: random.Random) -> str:
    kind = rng.randrange(8)
    i = rng.randrange(len(line))
    if kind == 0:
        return line[:i] + line[i + 1 :]  # drop a character
    if kind == 1:
        return line[:i] + " " + line[i:]  # stray space
    if kind == 2:
        return line[:i] + rng.choice("():,A -") + line[i:]
    if kind == 3:
        return line + " " + rng.choice(["EXTRA:1", "TYPE:x", "NAME:y z", "POS(1,2,3)", "(1,2,3)", "X(("])
    if kind == 4:
        parts = line.split(" ")
        rng.shuffle(parts)
        return " ".join(parts)
    if kind == 5:  # a free-text value that contains the next key (the nickname case)
        return line.replace("NAME:", "NAME:a TYPE:b COUNTRY:1 ", 1).replace("TYPE:", "TYPE:q COUNTRY:5 NAME:", 1)
    if kind == 6:
        return line.replace(":", ": ", 1)
    return line.replace(" ", "  ", 1)


@pytest.mark.parametrize("path", FIXTURES, ids=[p.name for p in FIXTURES])
def test_fast_path_equals_tokenizer_on_real_lines(path: Path) -> None:
    for line in fixture_lines(path):
        check(line)


@pytest.mark.parametrize("path", FIXTURES, ids=[p.name for p in FIXTURES])
def test_fast_path_equals_tokenizer_on_mutated_lines(path: Path) -> None:
    rng = random.Random(20261004)
    lines = fixture_lines(path)
    for line in rng.sample(lines, min(len(lines), 6000)):
        check(mutate(line, rng))


def test_free_text_containing_the_next_key_is_cut_like_the_tokenizer() -> None:
    check("T:1 AType:12 ID:5 TYPE:Tu COUNTRY:5 NAME:x COUNTRY:6 PID:-1 POS(1,2,3) MID:-1")
    check("T:1 AType:12 ID:5 TYPE:Tu COUNTRY:501 NAME:a PID:1 PID:-1 POS(1,2,3)")
    check("T:1 AType:12 ID:5 TYPE:Tu  Tu COUNTRY:501 NAME:a b, c PID:-1 POS(1, 2, 3)")
