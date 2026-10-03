"""The synthetic world the browser tests look at (doc 08: Playwright end-to-end tests).

Built with the replay-result factories and the real `save_mission`, so every table, aggregate and Elo rating is
filled by the production code. All names are made up: real player data never enters the tests.

Run as a script (`python -m tests.e2e.world <data dir>`) in a child process: the e2e server is a separate process on its
own SQLite file, and keeping Django out of the test process also keeps it out of Playwright's event loop.
It prints the `World` as JSON, which the `world` fixture reads.
"""

import json
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path

PLAYER_ACE = "Ace Pilotsson"
PLAYER_ACEY = "Acey Jones"  # shares the prefix "Ace" with PLAYER_ACE: a search for "Ace" finds two players
PLAYER_BOB = "Bob Wingman"
PLAYER_CHARLIE = "Charlie Foxtrot"
PLAYER_DELTA = "Delta Dawn"
PLAYER_ECHO = "Echo Bravo"

FILLER_MISSIONS = 55
"""More missions than any sensible page size (pagination); each has two sorties so the lists have something in them."""


@dataclass(frozen=True)
class World:
    """What the tests need to know about the data: names as the pages show them, not database ids."""

    featured_mission_pk: int
    """The mission the story happens in: Ace shoots Delta down, Charlie shoots Bob down (Bob bails out)."""
    mission_count: int
    ace_pk: int
    ace_sortie_pk: int
    """Ace's sortie in the featured mission (landed, two kills)."""
    delta_sortie_pk: int
    """Delta's sortie in the featured mission (shot down by Ace)."""
    logs_mission_pk: int
    logs_player_pk: int
    logs_sortie_pk: int
    """A mission, a player and a sortie that came out of an anonymized fixture log through the real ingest pipeline."""
    ace: str = PLAYER_ACE
    acey: str = PLAYER_ACEY
    bob: str = PLAYER_BOB
    echo: str = PLAYER_ECHO
    charlie: str = PLAYER_CHARLIE
    delta: str = PLAYER_DELTA
    ace_aircraft: str = "MiG-15bis"
    bob_aircraft: str = "MiG-15bis"
    delta_aircraft: str = "F-86A-5"


def build() -> World:
    """Migrate the database (Django's settings find the data dir through `IL2KS_DATA_DIR`) and fill it."""
    import django
    from django.core.management import call_command
    from django.db import connection

    django.setup()
    with connection.cursor() as cursor:  # a throw-away database: skip the fsyncs (minutes faster on Windows)
        cursor.execute("PRAGMA synchronous=OFF")
        cursor.execute("PRAGMA journal_mode=MEMORY")
    call_command("migrate", verbosity=0)

    from tests import factories as f

    featured = f.meta("2026-09-19_22-34-13", f.STARTED_AT)
    # Coalition 1 = REDFOR (MiG-15bis), 2 = BLUFOR (F-86A Sabre). Indexes are positions in the tuple.
    sorties = (
        f.sortie(0, 1, name=PLAYER_ACE, kills_air=2, assists=1, flight_time_s=1500.0),
        f.sortie(
            1, 2, name=PLAYER_BOB, outcome="shot_down", pilot_fate="bailed_out", is_plane_lost=True, damage_taken=1.0
        ),
        f.sortie(
            2,
            3,
            name=PLAYER_CHARLIE,
            aircraft_type="F-86A-5",
            coalition=2,
            kills_air=1,
            kills_ground=3,
            flight_time_s=1200.0,
        ),
        f.sortie(
            3,
            4,
            name=PLAYER_DELTA,
            aircraft_type="F-86A-5",
            coalition=2,
            outcome="shot_down",
            is_death=True,
            pilot_fate="in_aircraft",
            is_plane_lost=True,
            damage_taken=1.0,
        ),
        f.sortie(4, 5, name=PLAYER_ACEY, flight_time_s=900.0),
    )
    kills = (
        f.kill(2000, killer=0, victim=3, victim_type="F-86A-5", killer_type="MiG-15bis"),
        f.kill(3000, killer=0, victim=None, victim_type="F-86A-5", killer_type="MiG-15bis", credit="assist"),
        f.kill(4000, killer=2, victim=1, victim_type="MiG-15bis", killer_type="F-86A-5"),
        f.kill(5000, killer=0, victim=None, victim_type="F-86A-5", killer_type="MiG-15bis"),
    )
    f.save(f.mission(sorties, kills), featured)

    # Ace flies again in earlier missions, so the player's sortie list has more than one row.
    for i in range(FILLER_MISSIONS):
        started = f.STARTED_AT + timedelta(days=-1 - i, hours=i % 5)
        uid = started.strftime("%Y-%m-%d_%H-%M-%S")
        first = f.sortie(0, 1, name=PLAYER_ACE, kills_air=i % 3) if i % 7 == 0 else f.sortie(0, 6, name=PLAYER_ECHO)
        second = f.sortie(1, 7 + i % 4, name=f"Filler Pilot {7 + i % 4}", coalition=2, aircraft_type="F-86A-5")
        f.save(f.mission((first, second)), f.meta(uid, started))
    logs_uid = ingest_fixture_log()

    from il2ks.db.models import Mission, Player, PlayerSortie

    mission = Mission.objects.get(mission_uid=featured.mission_uid)
    logs_mission = Mission.objects.get(mission_uid=logs_uid)
    logs_sortie = PlayerSortie.objects.filter(mission=logs_mission).order_by("pk").first()
    assert logs_sortie is not None, "the fixture log produced no sorties"
    return World(
        featured_mission_pk=mission.pk,
        mission_count=FILLER_MISSIONS + 1,
        ace_pk=Player.objects.get(current_name=PLAYER_ACE).pk,
        ace_sortie_pk=PlayerSortie.objects.get(mission=mission, name_at_time=PLAYER_ACE).pk,
        delta_sortie_pk=PlayerSortie.objects.get(mission=mission, name_at_time=PLAYER_DELTA).pk,
        logs_mission_pk=logs_mission.pk,
        logs_player_pk=logs_sortie.player_id,
        logs_sortie_pk=logs_sortie.pk,
    )


def ingest_fixture_log() -> str:
    """Ingest `tests/fixtures/logs/typical.txt.zip` (anonymized) through the real parser, replay and persist.

    Returns its mission UID. Needs `build()` to have set Django up and migrated."""
    from django.conf import settings

    from il2ks.ingest.runner import IngestOptions, default_pipeline, ingest_once
    from tests.conftest import FIXTURE_LOGS
    from tests.ingest_fakes import make_config

    uid = "2026-09-01_10-00-00"
    cfg = make_config(Path(settings.DATA_DIR), None, after_archive="keep")
    with tempfile.TemporaryDirectory() as source:
        shutil.copy(FIXTURE_LOGS / "typical.txt.zip", Path(source) / f"missionReport({uid})[0].txt.zip")
        summary = ingest_once(cfg, default_pipeline(cfg), IngestOptions(source=Path(source)))
    assert summary.failed == [], f"ingesting the fixture log failed: {summary}"
    assert summary.ok == [uid]
    return uid


def main() -> None:
    print(json.dumps(asdict(build())))


if __name__ == "__main__":
    main()
