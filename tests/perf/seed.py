"""A bigger synthetic world for the performance tests (NFR-PERF-2) and for the load test.

Built with the replay-result factories and the real `save_mission`, so every aggregate is filled by production code.
Numbers are in the ballpark of a busy server's first months (NFR-PERF-3: ~73 player sorties per mission): the defaults
give a few thousand sorties, enough for lists to paginate and per-player pages to have real history, while seeding
stays at around a minute. Deterministic: the same arguments give the same data.

As a script (`python -m tests.perf.seed [missions [players [sorties per mission]]]`, with `IL2KS_DATA_DIR` pointing at
a scratch data dir) it migrates and fills the configured database and prints what it made as JSON
(see docs/performance-testing.md)."""

import json
import random
from dataclasses import asdict, dataclass
from datetime import timedelta

AIRCRAFT = ("MiG-15bis", "F-86A-5", "F-51D", "Il-10")
SIDE_OF = {"MiG-15bis": 1, "Il-10": 1, "F-86A-5": 2, "F-51D": 2}

DEFAULT_MISSIONS = 60
DEFAULT_PLAYERS = 250
DEFAULT_SORTIES_PER_MISSION = 30


@dataclass(frozen=True)
class SeededWorld:
    """Ids of interesting rows, for building URLs."""

    mission_pk: int
    player_pk: int
    sortie_pk: int
    aircraft_pk: int
    player_name: str
    mission_count: int
    sortie_count: int
    player_pks: list[int]
    mission_pks: list[int]
    sortie_pks: list[int]
    aircraft_pks: list[int]
    player_names: list[str]


def fill(
    missions: int = DEFAULT_MISSIONS,
    players: int = DEFAULT_PLAYERS,
    sorties_per_mission: int = DEFAULT_SORTIES_PER_MISSION,
    seed: int = 1951,
) -> SeededWorld:
    """Save `missions` missions (going back in time from `factories.STARTED_AT`) into the current database."""
    from django.db.models import Count
    from tests import factories as f

    from il2ks.core.replay.result import SortieResult
    from il2ks.db.models import GameObject, Mission, Player, PlayerSortie

    rng = random.Random(seed)
    for m in range(missions):
        started = f.STARTED_AT - timedelta(hours=7 * m)
        # One account flies at most once per mission here (the replay allows several; this keeps the data simple).
        pilots = rng.sample(range(players), min(players, sorties_per_mission))
        sorties: list[SortieResult] = []
        for i, who in enumerate(pilots):
            aircraft = AIRCRAFT[rng.randrange(len(AIRCRAFT))]
            fate = rng.random()
            lost = fate < 0.35
            sorties.append(
                f.sortie(
                    i,
                    who + 1,
                    name=f"Pilot {who + 1:03d}",
                    aircraft_type=aircraft,
                    coalition=SIDE_OF[aircraft],
                    kills_air=0 if lost else rng.randrange(3),
                    kills_ground=rng.randrange(4) if aircraft == "Il-10" else 0,
                    outcome="shot_down" if lost else "landed",
                    pilot_fate="bailed_out" if lost and fate < 0.2 else "in_aircraft",
                    is_plane_lost=lost,
                    is_death=lost and fate >= 0.2,
                    flight_time_s=300.0 + rng.randrange(3000),
                    damage_taken=1.0 if lost else 0.0,
                )
            )
        count = len(sorties)
        kills = tuple(
            f.kill(
                2000 + 100 * j,
                killer=j,
                victim=(j + 1) % count,
                victim_type=sorties[(j + 1) % count].aircraft_type,
                killer_type=sorties[j].aircraft_type,
            )
            for j in range(0, count - 1, 3)
            if sorties[j].coalition != sorties[(j + 1) % count].coalition
        )
        uid = started.strftime("%Y-%m-%d_%H-%M-%S")
        f.save(f.mission(tuple(sorties), kills), f.meta(uid, started))

    # The player with the most sorties: the heaviest per-player pages.
    busiest = Player.objects.annotate(n=Count("sortie_rows")).order_by("-n", "pk").first()
    assert busiest is not None
    latest = Mission.objects.order_by("-pk").first()
    assert latest is not None
    sortie = PlayerSortie.objects.filter(player=busiest).order_by("pk").first()
    assert sortie is not None
    aircraft_pks = list(GameObject.objects.filter(log_name__in=AIRCRAFT).values_list("pk", flat=True))
    return SeededWorld(
        mission_pk=latest.pk,
        player_pk=busiest.pk,
        sortie_pk=sortie.pk,
        aircraft_pk=aircraft_pks[0],
        player_name=busiest.current_name,
        mission_count=Mission.objects.count(),
        sortie_count=PlayerSortie.objects.count(),
        player_pks=list(Player.objects.order_by("pk").values_list("pk", flat=True)[:200]),
        mission_pks=list(Mission.objects.order_by("pk").values_list("pk", flat=True)[:100]),
        sortie_pks=list(PlayerSortie.objects.order_by("pk").values_list("pk", flat=True)[:300]),
        aircraft_pks=aircraft_pks,
        player_names=list(Player.objects.order_by("pk").values_list("current_name", flat=True)[:50]),
    )


def main() -> None:
    import sys

    import django
    from django.core.management import call_command

    django.setup()
    call_command("migrate", verbosity=0)
    numbers = [int(a) for a in sys.argv[1:4]]
    world = fill(*numbers)
    summary = {k: v for k, v in asdict(world).items() if not isinstance(v, list)}
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
