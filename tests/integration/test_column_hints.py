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
        (
            "/players/?q=&cols=elo_prop,score_air,friendly_kills_air,friendly_kills_ground",
            ["Elo (prop)", "Air score", "Friendly-fire air kills", "Friendly-fire ground kills"],
        ),
        (
            "/missions/?cols=friendly_kills_air,friendly_kills_ground",
            ["Friendly-fire air kills", "Friendly-fire ground kills"],
        ),
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
    described |= {"friendly_kills_air", "friendly_kills_ground", "damage_taken", "time_on_target"}
    for registry in registries:
        for column in registry:
            if column.key in described:
                assert column.description, column.key


REGISTRIES = (
    columns.PLAYER_COLUMNS,
    columns.MISSION_COLUMNS,
    columns.AIRCRAFT_COLUMNS,
    columns.SORTIE_COLUMNS,
    columns.SITE_SORTIE_COLUMNS,
    columns.MISSION_SORTIE_COLUMNS,
)
OBVIOUS = {"sorties", "mission", "tour"}  # the label says it all, and a header without a description stays plain


def test_no_column_shows_its_key_for_a_description() -> None:
    """Maintainer 2026-11-02: half of the tooltips of a sortie list read `air_points` and the like: a column without a
    description fell back to its own key."""
    for registry in REGISTRIES:
        for column in registry:
            assert column.description != column.key


def test_every_column_describes_itself_unless_the_label_says_it_all() -> None:
    for registry in REGISTRIES:
        for column in registry:
            assert column.description or column.key in OBVIOUS, column.key


@pytest.mark.parametrize(
    ("path", "labels"),
    [
        ("/leaderboards/air/", ["Air kills", "Deaths"]),
        ("/leaderboards/ground/", ["Ground kills"]),
        ("/leaderboards/ground-hour/", ["Attack ground score"]),
        ("/leaderboards/play-time/", ["Flight time"]),
        ("/missions/", ["Air kills", "Ground kills"]),
    ],
)
def test_the_default_headers_explain_the_counts_too(client: Client, path: str, labels: list[str]) -> None:
    """The audit of 2026-11-02: optional columns had descriptions the same column lacked as a default."""
    got = headers(client.get(path).content.decode())

    for label in labels:
        assert got.get(label), f"{path}: the header {label!r} has no description (headers: {sorted(got)})"


def test_the_air_board_describes_assists_on_aircraft() -> None:
    """Its number is air assists alone: the general text says ground objects count too."""
    client = Client()
    got = headers(client.get("/leaderboards/air/").content.decode())

    assert got["Assists"] == str(column_hints.HINTS["assists_air"])


def test_a_header_is_right_aligned_only_over_numbers() -> None:
    """ "Tour" holds a title, the one optional mission column that is no number."""
    tour = next(column for column in columns.MISSION_COLUMNS if column.key == "tour")
    assert not tour.numeric

    html = Client().get("/missions/?cols=tour").content.decode()
    header = next(head for head in re.findall(r"<th\b[^>]*>.*?</th>", html, re.S) if ">Tour<" in head)
    assert 'class="num' not in header


def test_the_descriptions_say_what_the_rules_do() -> None:
    """The audit of 2026-11-02 read every description against doc 13 and the code."""
    hint = column_hints.HINTS
    assert "a minute after the last" in str(hint["time_on_target"])
    assert "jet" in str(hint["elo_games"])
    assert "jet" in str(hint["elo_aircraft"]).lower()
    assert "flight-time points" in str(hint["air_points"])
    assert "ranked" not in str(hint["ground_hour"])  # aircraft tables rank nobody
    assert "gunners" in str(hint["sorties_per_player"])
    for key in ("friendly_kills_air", "friendly_kills_ground"):  # lists of many pilots, no "you" to speak to
        assert "you" not in str(hint[key]).lower().split()


def test_the_attack_share_of_an_aircraft_says_it_is_a_share() -> None:
    share = next(column for column in columns.AIRCRAFT_COLUMNS if column.key == "attack_share")

    assert str(share.label) == "Share of attack sorties"
    assert str(share.description).startswith("Share of")


def test_attack_proficiency_shows_one_decimal_everywhere() -> None:
    """As on its board and in the optional player column; the aircraft pages showed two."""
    assert render("{{ 1234|attack_rate:10800 }}") == "411.3"
    assert render("{{ 1234|attack_rate:0 }}") == render("{{ 1234|per_hour:0 }}")


def test_the_default_columns_of_the_sortie_lists_are_described() -> None:
    """The hints the templates name: a key that is not registered would show as the text."""
    for key in ("outcome", "pilot_fate", "kills_air", "kills_ground", "flight_time"):
        assert key in column_hints.HINTS


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
