"""Column descriptions (maintainer request 2026-10-04): a header whose meaning is not obvious carries a description that
hover, focus and tap show and a screen reader reads (components/col_th.html, `web.column_hints`)."""

import re

import pytest
from django.template import Context, Template
from django.test import Client

from il2ks.web import column_hints, columns

pytestmark = pytest.mark.django_db

HEADER = re.compile(r"<th\b[^>]*>(.*?)</th>", re.S)


def headers(html: str) -> dict[str, str]:
    """Header label -> the description text inside that header ('' without one), for the `<th>` of a page."""
    found: dict[str, str] = {}
    for body in HEADER.findall(html):
        tip = re.search(r'<span id="[^"]+" class="col-tip" role="tooltip">(.*?)</span>', body, re.S)
        label = re.sub(r"<[^>]+>", "", re.sub(r'<span id="[^"]+" class="col-tip".*?</span>', "", body, flags=re.S))
        found[re.sub(r"\s*\?\s*$", "", label.strip())] = tip.group(1) if tip else ""
    return found


@pytest.mark.parametrize(
    ("path", "labels"),
    [
        ("/leaderboards/elo-jet/", ["Elo", "Encounters"]),
        ("/leaderboards/air/", ["Air score", "Assists"]),
        ("/leaderboards/ground/", ["Ground score", "Attack sorties"]),
        ("/leaderboards/ground-hour/", ["Attack proficiency", "Time on target"]),
        ("/leaderboards/interception/", ["Kills per hour", "Bombers, attackers and transports shot down"]),
        ("/leaderboards/tank-busting/", ["Tanks per hour", "Tanks destroyed", "Time on target"]),
        (
            "/aircraft/?cols=accuracy_air,assists,hits",
            ["Elo", "K/L", "Survival", "Attack proficiency", "Air accuracy", "Hits to destroy"],
        ),
        ("/players/?q=&cols=elo_jet,kl,accuracy,assists_air,assists_ground", ["Elo (jet)", "K/L", "Gun accuracy"]),
        ("/players/?q=&cols=elo_prop,score_air,friendly_kills", ["Elo (prop)", "Air score", "Friendly kills"]),
        ("/missions/?cols=friendly_kills", ["Friendly kills"]),
    ],
)
def test_non_obvious_headers_carry_a_description(client: Client, path: str, labels: list[str]) -> None:
    got = headers(client.get(path).content.decode())

    for label in labels:
        assert got.get(label), f"{path}: the header {label!r} has no description (headers: {sorted(got)})"
    assert got.get("Sorties", "") == ""  # an obvious column stays plain
    assert got.get("Player", "") == ""


def test_every_registered_description_is_plain_text_and_every_optional_column_resolves() -> None:
    for key, text in column_hints.HINTS.items():
        assert str(text).strip(), key
        assert "<" not in str(text), key
    registries = (
        columns.PLAYER_COLUMNS,
        columns.MISSION_COLUMNS,
        columns.AIRCRAFT_COLUMNS,
        columns.SORTIE_COLUMNS,
        columns.MISSION_SORTIE_COLUMNS,
    )
    described = {
        "elo_jet",
        "elo_prop",
        "kd",
        "kl",
        "survival",
        "score_air",
        "score_ground",
        "ground_hour",
        "planes_lost",
    }
    described |= {"assists", "assists_air", "assists_ground", "accuracy", "accuracy_air", "accuracy_ground"}
    described |= {"friendly_kills", "damage_taken", "time_on_target"}
    for registry in registries:
        for column in registry:
            if column.key in described:
                assert column.description, column.key


def render(source: str) -> str:
    return Template("{% load il2ks %}" + source).render(Context({"sort": "", "request": None}))


def test_description_markup_is_accessible_and_ids_are_unique() -> None:
    html = render(
        '{% col_th "Elo" numeric=True hint="elo" %}{% col_th "Elo again" hint="elo" %}'
        '{% sort_th "kl" "K/L" numeric=True hint="kl" %}{% col_th "Plain" %}'
    )

    ids = re.findall(r'<span id="([^"]+)" class="col-tip"', html)
    assert len(ids) == 3
    assert len(set(ids)) == 3
    for tip_id in ids:
        assert f'aria-describedby="{tip_id}"' in html  # the label (or the sort link) is described by its tip
    assert 'class="col-hint__marker" aria-hidden="true"' in html
    assert "<button" not in html  # no interactive element inside the sort link or next to it
    assert '<th scope="col">Plain</th>' in html  # no description: an ordinary header


def test_the_matchup_ratio_hint_says_what_the_table_shows() -> None:
    """The matchup table shows "no losses" for a ratio with no losses and a dash below MIN_ENCOUNTERS kills plus
    losses (`aircraft/detail.html`); the hint must not claim a dash for the first case."""
    from il2ks.queries.aircraft import MIN_ENCOUNTERS

    text = str(column_hints.HINTS["kl_matchup"])
    assert "no losses" in text
    assert str(MIN_ENCOUNTERS) in text
