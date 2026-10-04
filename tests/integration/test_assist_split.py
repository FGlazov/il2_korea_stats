"""Assists split into air and ground assists (maintainer request 2026-10-04, doc 13): the score counts air assists
only, the profile and the sortie page show them apart, an upgraded database derives the split from the timelines."""

import re
import uuid
from pathlib import Path

import pytest
from django.test import Client

from il2ks.config import Config
from il2ks.core.ratings.score import DEFAULT_SCORE_RULES
from il2ks.db.models import GameObject, Player, PlayerSortie, SiteSettings
from il2ks.ops import migrate
from tests.factories import account, mission, save, sortie

pytestmark = pytest.mark.django_db


def test_only_air_assists_score() -> None:
    save(mission((sortie(0, 1, assists=1, assists_ground=9), sortie(1, 2, assists_ground=9))))

    assert Player.objects.get(account_uuid=account(1)).score_air == DEFAULT_SCORE_RULES.air_assist
    assert Player.objects.get(account_uuid=account(2)).score_air == 0.0


def test_the_counters_keep_assists_as_the_sum() -> None:
    save(mission((sortie(0, 1, assists=2, assists_ground=3),)))

    player = Player.objects.get(account_uuid=account(1))
    assert (player.assists, player.assists_air, player.assists_ground) == (5, 2, 3)
    row = PlayerSortie.objects.get()
    assert (row.assists, row.assists_air, row.assists_ground) == (5, 2, 3)


def test_the_profile_shows_air_assists_in_the_air_part_and_ground_assists_in_the_ground_part(client: Client) -> None:
    save(mission((sortie(0, 1, assists=2, assists_ground=7),)))
    player = Player.objects.get(account_uuid=account(1))

    html = client.get(f"/players/{player.pk}/?tour=all").content.decode()

    tiles = re.findall(r'stat-tile__value">([\d.,]+)</div>\s*<div class="stat-tile__label">Assists<', html)
    assert tiles == ["2", "7"]  # the air part comes first


def test_a_pilot_with_only_ground_assists_has_an_active_ground_part(client: Client) -> None:
    save(mission((sortie(0, 1, assists_ground=3),)))
    player = Player.objects.get(account_uuid=account(1))

    response = client.get(f"/players/{player.pk}/?tour=all")

    assert response.context["ground_active"] is True
    assert response.context["air_active"] is False


def test_the_sortie_page_summarises_the_split(client: Client) -> None:
    save(mission((sortie(0, 1, assists=2, assists_ground=7),)))

    html = client.get(f"/sorties/{PlayerSortie.objects.get().pk}/").content.decode()

    assert "2 air, 7 ground" in html


def test_the_optional_columns_show_both_kinds(client: Client) -> None:
    save(mission((sortie(0, 1, assists=2, assists_ground=7),)))

    html = client.get("/players/?cols=assists_air,assists_ground").content.decode()

    assert "Air assists" in html
    assert "Ground assists" in html


def test_the_backfill_derives_the_split_from_the_timelines_and_rebuilds_level_2() -> None:
    save(mission((sortie(0, 1, assists=4), sortie(1, 2), sortie(2, 3, assists=1))))
    GameObject.objects.update_or_create(log_name="B-29", defaults={"display_name": "B-29", "cls": "bomber"})
    GameObject.objects.update_or_create(log_name="M46 Patton", defaults={"display_name": "M46", "cls": "tank"})
    helper = PlayerSortie.objects.get(name_at_time="Player-2")
    mixed = PlayerSortie.objects.get(name_at_time="Player-1")
    mixed.timeline = [
        {"kind": "assist", "counterpart": {"object_type": "B-29", "sortie_id": None, "coalition": 1}},
        {"kind": "assist", "counterpart": {"object_type": "MiG-15bis", "sortie_id": helper.pk, "coalition": 1}},
        {"kind": "assist", "counterpart": {"object_type": "M46 Patton", "sortie_id": None, "coalition": 1}},
        {"kind": "assist", "counterpart": {"object_type": "M46 Patton", "sortie_id": None, "coalition": 1}},
        {"kind": "kill", "counterpart": {"object_type": "B-29", "sortie_id": None, "coalition": 1}},
    ]
    mixed.save(update_fields=["timeline"])
    lost = PlayerSortie.objects.get(name_at_time="Player-3")  # a timeline that lost its entry: the rest is ground
    PlayerSortie.objects.update(assists_air=0, assists_ground=0)
    Player.objects.update(assists_air=0, assists_ground=0, score_air=99.0)
    SiteSettings.objects.filter(pk=1).update(backfills_done=[])
    cfg = Config(data_dir=Path("."), server_uid=uuid.uuid4(), timezone_name="UTC")

    migrate._backfill_assist_split(cfg)  # pyright: ignore[reportPrivateUsage]

    mixed.refresh_from_db()
    lost.refresh_from_db()
    assert (mixed.assists_air, mixed.assists_ground) == (2, 2)
    assert (lost.assists_air, lost.assists_ground) == (0, 1)
    player = Player.objects.get(account_uuid=account(1))
    assert (player.assists_air, player.assists_ground) == (2, 2)
    assert player.score_air == 2 * DEFAULT_SCORE_RULES.air_assist
    assert migrate._already_done(migrate.BACKFILL_ASSIST_SPLIT)  # pyright: ignore[reportPrivateUsage]
