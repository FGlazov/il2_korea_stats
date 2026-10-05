"""Models: pre-aggregated read models (TD-08, doc 06). Level 1 = per mission, level 2 = across missions.

Natural keys (FR-ING-9, FR-WEB-13): mission `(server_uid, mission_uid)`, sortie `(mission, account_uuid, spawn_tick)`,
player `account_uuid`. Rows are upserted by them so PKs (and URLs) survive `reprocess`.
"""

from __future__ import annotations

from typing import ClassVar, Self

from django.db import models

from il2ks.db.validators import NAV_URL_MAX_LENGTH, validate_http_url


class HideableQuerySet[T: models.Model](models.QuerySet[T]):
    """Rows an admin can hide from public pages (`is_hidden`, FR-ADM-3). Page code starts from `visible()`."""

    def visible(self) -> Self:
        return self.filter(is_hidden=False)

    def hidden(self) -> Self:
        return self.filter(is_hidden=True)


class HideableManager[T: models.Model](models.Manager[T]):
    """`Player.objects.visible()` / `Mission.objects.visible()`: what public pages may list or link to."""

    def get_queryset(self) -> HideableQuerySet[T]:
        return HideableQuerySet(self.model, using=self._db)

    def visible(self) -> HideableQuerySet[T]:
        return self.get_queryset().visible()

    def hidden(self) -> HideableQuerySet[T]:
        return self.get_queryset().hidden()


class ObjectClass(models.TextChoices):
    FIGHTER = "fighter"
    ATTACKER = "attacker"
    BOMBER = "bomber"
    TRANSPORT = "transport"
    GUNNER = "gunner"
    VEHICLE = "vehicle"
    TANK = "tank"
    AAA = "aaa"
    SHIP = "ship"
    STATIC = "static"
    ORDNANCE = "ordnance"
    CREW = "crew"  # bots and crew groups
    EQUIPMENT = "equipment"  # parachutes, ejection seats, spotters, vehicle turrets
    UNKNOWN = "unknown"


class Propulsion(models.TextChoices):
    PROP = "prop"
    JET = "jet"


class GroundCategory(models.TextChoices):
    """What a ground kill is shown as (OQ-33). Values match `core.catalog.loader.GroundCategory`."""

    TANK = "tank"
    VEHICLE = "vehicle"
    ARTILLERY = "artillery"
    AAA = "aaa"
    SHIP = "ship"
    TRAIN = "train"
    BUILDING = "building"
    PARKED_AIRCRAFT = "parked_aircraft"
    OTHER = "other"


class GameObject(models.Model):
    """A game object type from the logs (aircraft, vehicle, ...). Unknown types are auto-registered (FR-ING-7)."""

    log_name = models.CharField(max_length=128, unique=True)
    display_name = models.CharField(max_length=128)  # the English name: the shipped default, or the admin's override
    name_overridden = models.BooleanField(default=False)  # True once an admin edited `display_name` (TD-24, FR-ADM-5)
    cls = models.CharField(max_length=16, choices=ObjectClass.choices, default=ObjectClass.UNKNOWN)
    propulsion = models.CharField(max_length=4, choices=Propulsion.choices, blank=True, default="")  # aircraft only
    ground_category = models.CharField(max_length=16, choices=GroundCategory.choices, blank=True, default="")
    is_playable = models.BooleanField(default=False)
    is_known = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(cls__in=ObjectClass.values),
                name="gameobject_cls_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(propulsion__in=[*Propulsion.values, ""]),
                name="gameobject_propulsion_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(ground_category__in=[*GroundCategory.values, ""]),
                name="gameobject_ground_category_valid",
            ),
            models.CheckConstraint(condition=~models.Q(log_name=""), name="gameobject_log_name_not_empty"),
        ]

    def __str__(self) -> str:
        return self.display_name or self.log_name


class Country(models.Model):
    """A country code from `CNTRS` (doc 06). Display names are admin-editable (FR-ADM-5)."""

    code = models.IntegerField(unique=True)
    coalition = models.IntegerField()
    display_name = models.CharField(max_length=64)

    class Meta:
        verbose_name_plural = "countries"

    def __str__(self) -> str:
        return self.display_name


# --- Choices shared by level-1 tables (values match core.replay.result Literals) ---


class Role(models.TextChoices):
    PILOT = "pilot"
    GUNNER = "gunner"


class SpawnType(models.TextChoices):
    AIR = "air"
    RUNWAY = "runway"
    PARKING = "parking"


class Outcome(models.TextChoices):
    LANDED = "landed"
    CRASHED = "crashed"
    SHOT_DOWN = "shot_down"
    DITCHED = "ditched"
    IN_FLIGHT = "in_flight"
    AIRBORNE = "airborne"  # still in the air when the mission ended
    NOT_TAKEN_OFF = "not_taken_off"
    UNKNOWN = "unknown"


class PilotFate(models.TextChoices):
    IN_AIRCRAFT = "in_aircraft"
    BAILED_OUT = "bailed_out"
    EXITED_ON_GROUND = "exited_on_ground"
    DISCONNECTED = "disconnected"
    UNKNOWN = "unknown"


class PilotFateSource(models.TextChoices):
    EVENT = "event"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class PilotStatus(models.TextChoices):
    HEALTHY = "healthy"
    WOUNDED = "wounded"
    DEAD = "dead"
    CAPTURED = "captured"


class AircraftStatus(models.TextChoices):
    UNHARMED = "unharmed"
    DAMAGED = "damaged"
    DESTROYED = "destroyed"


class LossCause(models.TextChoices):
    ATTACKER = "attacker"
    SELF = "self"
    NONE = "none"


class LossClass(models.TextChoices):
    """Who is behind a lost sortie (FR-WEB-21). Values match `core.replay.result.LossClass`."""

    PLAYER = "player"
    AI_AIRCRAFT = "ai_aircraft"
    AI_GUNNER = "ai_gunner"
    AAA = "aaa"
    GROUND = "ground"
    FRIENDLY = "friendly"
    ENVIRONMENT = "environment"
    UNKNOWN = "unknown"


class CombatRole(models.TextChoices):
    AIR_SUPERIORITY = "air_superiority"
    ATTACK = "attack"


class AircraftRole(models.TextChoices):
    """The role scope of an aircraft-type stats row (FR-WEB-8): every sortie, or only those of one combat role."""

    ALL = "all"
    AIR_SUPERIORITY = "air_superiority"
    ATTACK = "attack"


NO_MODS_RECORDED = -1
"""`MissionAircraftAmmo.weapon_mods` of a destroyed aircraft that was not a counted player sortie (AI, or a row from
before the role and mods were stored): it belongs to no modification pattern."""


def scoped_unique(name: str, fields: list[str]) -> list[models.UniqueConstraint]:
    """A unique key over `fields` plus a nullable `tour` (null = all time): SQL treats NULLs as distinct, so one
    conditional constraint covers the rows with a tour and one the all-time rows (the `AircraftMatchup` pattern)."""
    return [
        models.UniqueConstraint(
            fields=["tour", *fields], condition=models.Q(tour__isnull=False), name=f"{name}_tour_unique"
        ),
        models.UniqueConstraint(fields=fields, condition=models.Q(tour__isnull=True), name=f"{name}_alltime_unique"),
    ]


class KillCredit(models.TextChoices):
    KILL = "kill"
    ASSIST = "assist"
    SHARED = "shared"


class KillVia(models.TextChoices):
    DIRECT = "direct"
    ABANDONED_AIRCRAFT = "abandoned_aircraft"
    DISCONNECT = "disconnect"


TOTAL_AMMO = "*"
"""The `ammo` of the row that sums all gun ammo (`MissionAircraftAmmo`, `AircraftAmmoStats`)."""

HEAVY_SORTIE_COLUMNS = ("ammo", "damage_breakdown", "timeline")
"""The big JSON columns of `PlayerSortie`: only the sortie report renders them, so list queries `.defer()` them."""


class Counters(models.Model):
    """The counters shared by PlayerMission, Player and PlayerAircraft (doc 06, FR-WEB-4). No ratios (TD-22)."""

    sorties = models.PositiveIntegerField(default=0)
    flight_time_air_s = models.FloatField(default=0.0)
    flight_time_s = models.FloatField(default=0.0)
    kills_air = models.PositiveIntegerField(default=0)
    kills_ground = models.PositiveIntegerField(default=0)
    assists = models.PositiveIntegerField(default=0)  # = assists_air + assists_ground
    assists_air = models.PositiveIntegerField(default=0)
    assists_ground = models.PositiveIntegerField(default=0)
    deaths = models.PositiveIntegerField(default=0)
    planes_lost = models.PositiveIntegerField(default=0)
    bailouts = models.PositiveIntegerField(default=0)
    suspected_early_bailouts = models.PositiveIntegerField(default=0)
    captures = models.PositiveIntegerField(default=0)
    takeoffs = models.PositiveIntegerField(default=0)
    landings = models.PositiveIntegerField(default=0)
    # Friendly fire is tracked apart from the counters above (never in kills_*, assists)
    friendly_kills = models.PositiveIntegerField(default=0)
    friendly_hits = models.PositiveIntegerField(default=0)
    friendly_damage = models.FloatField(default=0.0)
    # Ground losses, combat role and time on target (FR-WEB-19/20)
    taxi_accidents = models.PositiveIntegerField(default=0)
    strafed_on_ground = models.PositiveIntegerField(default=0)
    attack_sorties = models.PositiveIntegerField(default=0)
    time_on_target_s = models.FloatField(default=0.0)
    # Ground kills by category (they sum to kills_ground) and how many of those were static objects (OQ-33, doc 13)
    kills_tank_attack = models.PositiveIntegerField(default=0)  # tanks in attack sorties (tank busting)
    kills_ground_tank = models.PositiveIntegerField(default=0)
    kills_ground_vehicle = models.PositiveIntegerField(default=0)
    kills_ground_artillery = models.PositiveIntegerField(default=0)
    kills_ground_aaa = models.PositiveIntegerField(default=0)
    kills_ground_ship = models.PositiveIntegerField(default=0)
    kills_ground_train = models.PositiveIntegerField(default=0)
    kills_ground_building = models.PositiveIntegerField(default=0)
    kills_ground_parked_aircraft = models.PositiveIntegerField(default=0)
    kills_ground_other = models.PositiveIntegerField(default=0)
    kills_ground_static = models.PositiveIntegerField(default=0)
    # PvE breakdown (FR-WEB-21): deaths and lost aircraft by who is behind them (each family sums to deaths /
    # planes_lost, tested), and air kills split by victim (kills_air_pvp + kills_air_ai == kills_air)
    kills_air_pvp = models.PositiveIntegerField(default=0)
    kills_air_ai = models.PositiveIntegerField(default=0)
    deaths_by_player = models.PositiveIntegerField(default=0)
    deaths_by_ai_aircraft = models.PositiveIntegerField(default=0)
    deaths_by_ai_gunner = models.PositiveIntegerField(default=0)
    deaths_by_aaa = models.PositiveIntegerField(default=0)
    deaths_by_ground = models.PositiveIntegerField(default=0)
    deaths_by_friendly = models.PositiveIntegerField(default=0)
    deaths_by_environment = models.PositiveIntegerField(default=0)
    deaths_by_unknown = models.PositiveIntegerField(default=0)
    planes_lost_by_player = models.PositiveIntegerField(default=0)
    planes_lost_by_ai_aircraft = models.PositiveIntegerField(default=0)
    planes_lost_by_ai_gunner = models.PositiveIntegerField(default=0)
    planes_lost_by_aaa = models.PositiveIntegerField(default=0)
    planes_lost_by_ground = models.PositiveIntegerField(default=0)
    planes_lost_by_friendly = models.PositiveIntegerField(default=0)
    planes_lost_by_environment = models.PositiveIntegerField(default=0)
    planes_lost_by_unknown = models.PositiveIntegerField(default=0)
    # Score (FR-WEB-7): sums of the sorties' air and ground scores (`core.ratings.score`), and the part of the ground
    # score earned in attack sorties, which divided by `time_on_target_s` is the ground proficiency (FR-WEB-20)
    score_air = models.FloatField(default=0.0)
    score_ground = models.FloatField(default=0.0)
    score_ground_attack = models.FloatField(default=0.0)
    # Skill boards (doc 13): air-superiority sorties and their kills of
    # bombers, attackers and transports (interception per
    # hour of their flight time, `flight_time_air_s`, declared before `flight_time_s` like in the registry: an
    # annotation shadows the field of the same name); tanks destroyed in attack sorties (`kills_tank_attack`)
    air_superiority_sorties = models.PositiveIntegerField(default=0)
    kills_intercept = models.PositiveIntegerField(default=0)
    # Accuracy (doc 13): rounds fired and the gun hits of the SAME sorties (those with a known number of rounds, so
    # resupplied or unreliable ones are in neither), overall and for air-superiority sorties (air hits) and attack
    # sorties (ground hits); and the exact gun hits of all sorties. Ratios are computed on read.
    accuracy_rounds = models.PositiveIntegerField(default=0)
    accuracy_hits = models.PositiveIntegerField(default=0)
    accuracy_air_rounds = models.PositiveIntegerField(default=0)
    accuracy_air_hits = models.PositiveIntegerField(default=0)
    accuracy_ground_rounds = models.PositiveIntegerField(default=0)
    accuracy_ground_hits = models.PositiveIntegerField(default=0)
    gun_hits_air = models.PositiveIntegerField(default=0)
    gun_hits_ground = models.PositiveIntegerField(default=0)

    class Meta:
        abstract = True


# --- Level 2 identity (needed by level 1 FKs) ---


class Player(Counters):
    """A game account (doc 06). All-time counters are level 2: incremental at ingest, rebuildable from level 1."""

    account_uuid = models.CharField(max_length=36, unique=True)
    current_name = models.CharField(max_length=128)
    name_lower = models.CharField(max_length=128, db_index=True)
    first_seen = models.DateTimeField()
    last_seen = models.DateTimeField()
    is_hidden = models.BooleanField(default=False)
    # All-time air-to-air Elo (OQ-28, FR-WEB-19, OQ-128): the best of the player's tours (the highest final rating of a
    # tour, per pool) and the games summed over all tours. Ratings are replayed per tour (`PlayerTourPool.elo`) and
    # reset at every new tour, so this is derived by `ingest.ratings.recompute_ratings`, never a Counters sum.
    # Defaults are the config's `[ratings] start` and 0 games.
    elo_prop = models.FloatField(default=1500.0)
    elo_jet = models.FloatField(default=1500.0)
    elo_prop_games = models.PositiveIntegerField(default=0)
    elo_jet_games = models.PositiveIntegerField(default=0)
    # The player list's two streak columns (maintainer 2026-10-05), copied from the all-time `PlayerBestStreak` rows by
    # `ingest.streaks.rollup_streaks` so the list needs no join: the air kills of the best air ironman run (by air
    # kills) and the ground kills of the best ground ironman run (by ground kills). Both 0 without such a run.
    streak_kills_air = models.PositiveIntegerField(default=0)
    streak_kills_ground = models.PositiveIntegerField(default=0)

    objects: ClassVar[HideableManager[Player]] = HideableManager()  # pyright: ignore[reportIncompatibleVariableOverride]

    class Meta(Counters.Meta):
        abstract = False
        # Query-plan rule (docs/performance-testing.md, tests/perf/test_query_plans.py): every board and list orders by
        # an indexed column. The all-time boards order by `score DESC, name_lower ASC` (the tie-break is the player's
        # name), so the index has exactly that order, and carries the minimum-activity filter columns and `is_hidden`
        # so the page query and its COUNT never touch the table to filter. The per-hour boards sort by a ratio no index
        # can serve: their covering indexes only make the filter and the count a range scan.
        indexes = [
            models.Index(fields=["-score_air", "name_lower", "sorties", "is_hidden"], name="player_board_air"),
            models.Index(fields=["-score_ground", "name_lower", "sorties", "is_hidden"], name="player_board_ground"),
            models.Index(fields=["-flight_time_s", "name_lower", "is_hidden"], name="player_board_playtime"),
            models.Index(fields=["-elo_jet", "name_lower", "elo_jet_games", "is_hidden"], name="player_board_elo_jet"),
            models.Index(
                fields=["-elo_prop", "name_lower", "elo_prop_games", "is_hidden"], name="player_board_elo_prop"
            ),
            models.Index(
                fields=["air_superiority_sorties", "flight_time_air_s", "is_hidden"], name="player_board_intercept"
            ),
            models.Index(fields=["attack_sorties", "time_on_target_s", "is_hidden"], name="player_board_ground_hour"),
            # The player list: its default sort (last seen) and air kills, the column visitors sort by most.
            models.Index(fields=["-last_seen", "is_hidden"], name="player_list_last_seen"),
            models.Index(fields=["-kills_air", "is_hidden"], name="player_list_kills_air"),
        ]

    def __str__(self) -> str:
        return self.current_name


class PlayerName(models.Model):
    """Nickname history, so search finds old names (FR-WEB-3)."""

    player_id: int
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="names")
    name = models.CharField(max_length=128)
    name_lower = models.CharField(max_length=128, db_index=True)
    first_seen = models.DateTimeField()
    last_seen = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["player", "name"], name="playername_unique")]

    def __str__(self) -> str:
        return self.name


# --- Tours (TD-26): periods that group missions ---


class Tour(models.Model):
    """A period of missions (TD-26, FR-WEB-10): `[started_at, ended_at)`, ended_at null while it is open.

    In `monthly` / `days:<N>` mode the boundaries come from the calendar (`core.tours`) and `ended_at` is set at
    creation (it may lie in the future for the current tour). In `manual` mode the newest tour is open (`ended_at` null)
    until an admin starts the next one (FR-ADM-8). `mode` is the `[tours] mode` the tour was created under; a tour
    whose mode differs from the configured one is stale until `rebuild-aggregates --retour`. `title` is
    admin-editable (renames survive rebuilds)."""

    title = models.CharField(max_length=100)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True)
    mode = models.CharField(max_length=16)  # "monthly", "days:14", "manual"
    # Started because a mission was won by one side (the admin's "new tour after a decisive mission" option, TD-26):
    # a part of the period the mode draws, not a boundary of its own. Manual mode's own boundaries are the tours
    # where this is False.
    by_win = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["started_at"], name="tour_started_at_unique"),
            models.CheckConstraint(
                condition=models.Q(ended_at__isnull=True) | models.Q(ended_at__gt=models.F("started_at")),
                name="tour_ends_after_start",
            ),
        ]
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return self.title


class PlayerTourName(models.Model):
    """Level 2 identity within one tour (doc 14 "Level 2 is per tour"): one row per (player, tour, name used), filled
    from the sorties of ALL roles in that tour's missions (a gunner-only player has identity rows but no `PlayerTour`).
    `first_seen` = earliest spawn, `last_seen` = latest sortie end, `last_spawn` = latest spawn under this name.
    `Player.first_seen` / `last_seen` / `current_name` and the `PlayerName` history are the MIN / MAX / latest-spawn
    roll-up of these rows (`ingest.aggregates`), so the all-time identity never reads the whole sortie history."""

    player_id: int
    tour_id: int

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="tour_names")
    tour = models.ForeignKey(Tour, on_delete=models.CASCADE, related_name="player_names")
    name = models.CharField(max_length=128)
    first_seen = models.DateTimeField()
    last_seen = models.DateTimeField()
    last_spawn = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["player", "tour", "name"], name="playertourname_unique")]

    def __str__(self) -> str:
        return f"{self.player_id} / tour {self.tour_id} / {self.name}"


# --- Level 1: per mission ---


class Mission(models.Model):
    tour_id: int | None

    server_uid = models.UUIDField()
    mission_uid = models.CharField(max_length=32)  # file name timestamp, e.g. "2026-09-19_22-34-13" (TD-15)
    mission_file = models.CharField(max_length=255)
    file_path = models.CharField(max_length=500, blank=True)  # archive path
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField()
    duration_s = models.FloatField()
    game_date = models.CharField(max_length=32)
    game_time = models.CharField(max_length=32)
    game_type = models.IntegerField()
    settings: models.JSONField[dict[str, str]] = models.JSONField(default=dict)
    countries: models.JSONField[dict[str, int]] = models.JSONField(default=dict)  # {"501": 1, ...} from CNTRS
    log_version = models.IntegerField(null=True)
    completed_cleanly = models.BooleanField()
    winning_coalition = models.IntegerField(null=True)
    # "win" (`winning_coalition` is the side), "draw" (objectives reported, no sole winner) or "unknown" (no report, or
    # saved before this column existed until `il2ks reprocess`). Decisive = `result == "win"` (an "unknown" row with a
    # winner is an old, unread one).
    result = models.CharField(max_length=8, default="unknown")
    is_hidden = models.BooleanField(default=False)
    # Provisional: the mission is still running and `watch` saved its sorties so far (FR-ING-15). The final save (the
    # normal ingest once the mission is complete) rewrites the same rows by the same natural keys and clears this.
    is_live = models.BooleanField(default=False)
    # The tour containing `started_at` (TD-26). Null only for missions saved before tours existed, until the next
    # `rebuild-aggregates`.
    tour = models.ForeignKey("Tour", on_delete=models.PROTECT, null=True, related_name="missions")
    # Pre-aggregated for list/detail pages
    players_total = models.PositiveIntegerField(default=0)
    sorties_total = models.PositiveIntegerField(default=0)
    redfor_sorties = models.PositiveIntegerField(default=0)
    blufor_sorties = models.PositiveIntegerField(default=0)
    kills_air = models.PositiveIntegerField(default=0)
    kills_ground = models.PositiveIntegerField(default=0)
    friendly_kills = models.PositiveIntegerField(default=0)

    objects: ClassVar[HideableManager[Mission]] = HideableManager()  # pyright: ignore[reportIncompatibleVariableOverride]

    class Meta:
        constraints = [models.UniqueConstraint(fields=["server_uid", "mission_uid"], name="mission_natural_key")]
        # The mission list and the home page: newest first, all time or within a tour; the filters of the default list
        # (`is_hidden`, `sorties_total`) ride along so the list and its COUNT are answered from the index.
        indexes = [
            models.Index(fields=["-started_at", "is_hidden", "sorties_total"], name="mission_started_list"),
            models.Index(
                fields=["tour", "-started_at", "is_hidden", "sorties_total"], name="mission_tour_started_list"
            ),
        ]

    def __str__(self) -> str:
        return self.mission_uid


class PlayerSortie(models.Model):
    mission_id: int
    player_id: int
    aircraft_id: int
    mission = models.ForeignKey(Mission, on_delete=models.CASCADE, related_name="sortie_rows")
    player = models.ForeignKey(Player, on_delete=models.PROTECT, related_name="sortie_rows")
    account_uuid = models.CharField(max_length=36)  # part of the natural key
    spawn_tick = models.IntegerField()  # part of the natural key
    name_at_time = models.CharField(max_length=128)
    profile_uuid = models.CharField(max_length=36)
    aircraft = models.ForeignKey(GameObject, on_delete=models.PROTECT, related_name="sortie_rows")
    coalition = models.IntegerField()
    country = models.IntegerField()
    role = models.CharField(max_length=8, choices=Role.choices)
    spawned_at = models.DateTimeField()
    took_off_at = models.DateTimeField(null=True)
    landed_at = models.DateTimeField(null=True)
    ended_at = models.DateTimeField()
    flight_time_s = models.FloatField(default=0.0)
    air_start = models.BooleanField(default=False)
    spawn_type = models.CharField(max_length=8, choices=SpawnType.choices)
    payload_id = models.IntegerField(default=0)
    payload_name = models.CharField(max_length=128, blank=True)
    weapon_mods = models.IntegerField(default=0)
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    pilot_fate = models.CharField(max_length=20, choices=PilotFate.choices)
    pilot_fate_source = models.CharField(max_length=10, choices=PilotFateSource.choices)
    pilot_status = models.CharField(max_length=10, choices=PilotStatus.choices)
    suspected_early_bailout = models.BooleanField(default=False)
    aircraft_status = models.CharField(max_length=10, choices=AircraftStatus.choices)
    damage_taken = models.FloatField(default=0.0)  # aircraft damage; 1.0 when destroyed (OQ-115)
    # Pilot (gunner) damage, 1.0 when dead; health = 1 - this. NULL = unknown (sorties from before migration 0050 that
    # did not die: `reprocess --all` fills it).
    pilot_damage = models.FloatField(null=True, default=None)
    disconnected = models.BooleanField(default=False)
    is_death = models.BooleanField(default=False)
    is_plane_lost = models.BooleanField(default=False)
    is_captured = models.BooleanField(default=False)
    loss_cause = models.CharField(max_length=10, choices=LossCause.choices)
    suspected_structural_failure = models.BooleanField(default=False)
    kills_air = models.PositiveIntegerField(default=0)
    kills_ground = models.PositiveIntegerField(default=0)
    assists = models.PositiveIntegerField(default=0)  # = assists_air + assists_ground
    assists_air = models.PositiveIntegerField(default=0)
    assists_ground = models.PositiveIntegerField(default=0)
    takeoffs = models.PositiveIntegerField(default=0)
    landings = models.PositiveIntegerField(default=0)
    friendly_kills = models.PositiveIntegerField(default=0)
    friendly_hits = models.PositiveIntegerField(default=0)
    friendly_damage = models.FloatField(default=0.0)
    resupplied = models.BooleanField(default=False)  # FR-ING-24: a landing followed by another takeoff
    # Accuracy (doc 13): rounds fired is NULL where unknown (resupplied, unreliable AType 4, gunners); gun hits
    # (bullets and shells) are exact. Level 2 counts the hits only of sorties with a known number of rounds.
    rounds_fired = models.PositiveIntegerField(null=True, blank=True)
    gun_hits_air = models.PositiveIntegerField(default=0)
    gun_hits_ground = models.PositiveIntegerField(default=0)
    taxi_accident = models.BooleanField(default=False)  # lost before the first takeoff to its own doing
    strafed_on_ground = models.BooleanField(default=False)  # destroyed on the ground by an attacker
    ended_by_mission_end = models.BooleanField(
        default=False
    )  # the server force-ended it; outcome = state at that moment
    # None for gunners (not "": a gunner has no role, which is different from an unknown one)
    combat_role = models.CharField(max_length=16, choices=CombatRole.choices, null=True)  # noqa: DJ001
    time_on_target_s = models.FloatField(null=True)  # attack sorties only
    # Ground kills by category (they sum to kills_ground) and how many were static objects (OQ-33, doc 13)
    kills_ground_tank = models.PositiveIntegerField(default=0)
    kills_ground_vehicle = models.PositiveIntegerField(default=0)
    kills_ground_artillery = models.PositiveIntegerField(default=0)
    kills_ground_aaa = models.PositiveIntegerField(default=0)
    kills_ground_ship = models.PositiveIntegerField(default=0)
    kills_ground_train = models.PositiveIntegerField(default=0)
    kills_ground_building = models.PositiveIntegerField(default=0)
    kills_ground_parked_aircraft = models.PositiveIntegerField(default=0)
    kills_ground_other = models.PositiveIntegerField(default=0)
    kills_ground_static = models.PositiveIntegerField(default=0)
    # PvE breakdown (FR-WEB-21): who is behind the loss ("" = nothing lost) and the air kills by victim
    loss_class = models.CharField(max_length=12, choices=LossClass.choices, blank=True, default="")
    kills_air_pvp = models.PositiveIntegerField(default=0)
    kills_air_ai = models.PositiveIntegerField(default=0)
    kills_air_intercept = models.PositiveIntegerField(default=0)  # air kills of bombers / attackers (part of kills_air)
    # Achievement facts (doc 17): air kills by ramming an enemy (part of kills_air); the sortie made the first credited
    # PvP air kill of its mission; the most air kills within `core.replay.kills.BURST_WINDOW_S` seconds; and the highest
    # Elo (prop or jet) the pilot held after a win in this sortie, 0 without one (written by `ingest.ratings`).
    rams = models.PositiveIntegerField(default=0)
    first_blood = models.BooleanField(default=False)
    multi_kill = models.PositiveIntegerField(default=0)
    elo_peak = models.FloatField(default=0.0)
    # Air and ground score of this sortie (pilots; gunners 0). Named `*_points` so the counters `score_*` can sum them.
    # Computed from the columns above and the `[score]` rules; a changed rule is applied by `il2ks rebuild-aggregates`.
    air_points = models.FloatField(default=0.0)
    ground_points = models.FloatField(default=0.0)
    ammo: models.JSONField[dict[str, object]] = models.JSONField(default=dict)
    damage_breakdown: models.JSONField[list[dict[str, object]]] = models.JSONField(default=list)
    timeline: models.JSONField[list[dict[str, object]]] = models.JSONField(default=list)
    pos_spawn_x = models.FloatField(null=True)
    pos_spawn_y = models.FloatField(null=True)
    pos_spawn_z = models.FloatField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["mission", "account_uuid", "spawn_tick"], name="sortie_natural_key"),
            models.CheckConstraint(condition=models.Q(role__in=Role.values), name="sortie_role_valid"),
            models.CheckConstraint(condition=models.Q(spawn_type__in=SpawnType.values), name="sortie_spawn_type_valid"),
            models.CheckConstraint(condition=models.Q(outcome__in=Outcome.values), name="sortie_outcome_valid"),
            models.CheckConstraint(condition=models.Q(pilot_fate__in=PilotFate.values), name="sortie_pilot_fate_valid"),
            models.CheckConstraint(
                condition=models.Q(pilot_fate_source__in=PilotFateSource.values), name="sortie_pilot_fate_source_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(pilot_status__in=PilotStatus.values), name="sortie_pilot_status_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(aircraft_status__in=AircraftStatus.values), name="sortie_aircraft_status_valid"
            ),
            models.CheckConstraint(condition=models.Q(loss_cause__in=LossCause.values), name="sortie_loss_cause_valid"),
            models.CheckConstraint(
                condition=models.Q(loss_class__in=[*LossClass.values, ""]), name="sortie_loss_class_valid"
            ),
            models.CheckConstraint(
                condition=models.Q(combat_role__isnull=True) | models.Q(combat_role__in=CombatRole.values),
                name="sortie_combat_role_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(damage_taken__gte=0) & models.Q(damage_taken__lte=1),
                name="sortie_damage_taken_range",
            ),
            models.CheckConstraint(condition=models.Q(flight_time_s__gte=0), name="sortie_flight_time_nonneg"),
        ]
        indexes = [models.Index(fields=["player", "-spawned_at"], name="sortie_player_recent")]

    def __str__(self) -> str:
        return f"{self.name_at_time} @ {self.mission_id}"

    @property
    def pilot_health(self) -> float | None:
        """0..1 health left of the pilot (gunner), None where the damage is unknown (see `pilot_damage`)."""
        return None if self.pilot_damage is None else 1.0 - self.pilot_damage


class Kill(models.Model):
    """PvP only `[DECIDED]`: killer and victim are both player sorties (doc 06).

    One row per `(victim_sortie, killer_sortie)`: a victim sortie is lost once, and a killer sortie gets either the kill
    or an assist on it, never both. `credit` and `tick` are plain attributes, updated when the row is upserted."""

    mission_id: int
    killer_sortie_id: int
    victim_sortie_id: int

    mission = models.ForeignKey(Mission, on_delete=models.CASCADE, related_name="kills")
    tick = models.IntegerField()
    time = models.DateTimeField()
    killer_sortie = models.ForeignKey(PlayerSortie, on_delete=models.CASCADE, related_name="kills_made")
    victim_sortie = models.ForeignKey(PlayerSortie, on_delete=models.CASCADE, related_name="kills_suffered")
    is_friendly = models.BooleanField(default=False)
    credit = models.CharField(max_length=8, choices=KillCredit.choices)
    via = models.CharField(max_length=20, choices=KillVia.choices)
    pos_x = models.FloatField(null=True)
    pos_y = models.FloatField(null=True)
    pos_z = models.FloatField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["victim_sortie", "killer_sortie"], name="kill_natural_key"),
            models.CheckConstraint(condition=models.Q(credit__in=KillCredit.values), name="kill_credit_valid"),
            models.CheckConstraint(condition=models.Q(via__in=KillVia.values), name="kill_via_valid"),
        ]

    def __str__(self) -> str:
        return f"{self.killer_sortie_id} -> {self.victim_sortie_id}"


class PlayerMission(Counters):
    """Level 1: a player's totals for one mission (doc 06)."""

    player_id: int
    mission_id: int

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="missions")
    mission = models.ForeignKey(Mission, on_delete=models.CASCADE, related_name="player_missions")
    coalition = models.IntegerField()

    class Meta(Counters.Meta):
        abstract = False
        constraints = [models.UniqueConstraint(fields=["player", "mission"], name="playermission_unique")]

    def __str__(self) -> str:
        return f"{self.player_id} @ {self.mission_id}"


class MissionAircraftAmmo(models.Model):
    """Level 1 (FR-WEB-18, doc 06): gun hits that destroyed aircraft of one type in one mission.

    From the aircraft kills where all the damage came from one attacker (replay `SingleAttackerKill`), any victim and
    attacker, players or AI. One row per `(mission, aircraft, ammo)`; `ammo` is a gun ammo name, or `TOTAL_AMMO` for all
    gun ammo together. `kills` = such kills where that ammo hit at least once (every counted kill for `TOTAL_AMMO`),
    `hits` = hit lines of that ammo on those aircraft. Rewritten whenever the mission is saved."""

    mission_id: int
    aircraft_id: int

    mission = models.ForeignKey(Mission, on_delete=models.CASCADE, related_name="aircraft_ammo")
    aircraft = models.ForeignKey(GameObject, on_delete=models.PROTECT, related_name="mission_ammo")
    # The destroyed aircraft's own sortie, when it was a counted player sortie: its combat role ('' = none or not a
    # player) and weapon mods (`NO_MODS_RECORDED` = not a player sortie). The stats scopes of the aircraft page follow
    # them.
    combat_role = models.CharField(max_length=16, blank=True, default="")
    weapon_mods = models.IntegerField(default=NO_MODS_RECORDED)
    ammo = models.CharField(max_length=128)
    kills = models.PositiveIntegerField(default=0)
    hits = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["mission", "aircraft", "combat_role", "weapon_mods", "ammo"], name="missionaircraftammo_unique"
            )
        ]

    def __str__(self) -> str:
        return f"{self.mission_id} / {self.aircraft_id} / {self.ammo}"


MIX_SEPARATOR = "|"
"""Joins the sorted gun ammo log names of an ammo mix into its key (`AmmoMixKey`); no ammo name contains it."""

MIX_KEY_LENGTH = 512


class MissionAircraftAmmoMix(models.Model):
    """Level 1 (FR-WEB-18, doc 06): the same single-attacker kills as `MissionAircraftAmmo`, grouped by the set of gun
    ammo types that hit (an ammo mix). `mix` is the sorted ammo log names joined by `MIX_SEPARATOR`. One row per
    `(mission, aircraft, mix, ammo)`: `ammo` is a member of the mix with the hits it landed, or `TOTAL_AMMO` with the
    total hits. `kills` = instances of the mix (equal on all rows of one mix). Rewritten when the mission is saved."""

    mission_id: int
    aircraft_id: int

    mission = models.ForeignKey(Mission, on_delete=models.CASCADE, related_name="aircraft_ammo_mixes")
    aircraft = models.ForeignKey(GameObject, on_delete=models.PROTECT, related_name="mission_ammo_mixes")
    combat_role = models.CharField(max_length=16, blank=True, default="")  # as on `MissionAircraftAmmo`
    weapon_mods = models.IntegerField(default=NO_MODS_RECORDED)
    mix = models.CharField(max_length=MIX_KEY_LENGTH)
    ammo = models.CharField(max_length=128)
    kills = models.PositiveIntegerField(default=0)
    hits = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["mission", "aircraft", "combat_role", "weapon_mods", "mix", "ammo"],
                name="missionaircraftammomix_unique",
            )
        ]

    def __str__(self) -> str:
        return f"{self.mission_id} / {self.aircraft_id} / {self.mix} / {self.ammo}"


# --- Level 2: across missions ---


class PlayerAircraft(Counters):
    """All-time counters per player and aircraft type (profile table, FR-WEB-4)."""

    player_id: int
    aircraft_id: int

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="aircraft_stats")
    aircraft = models.ForeignKey(GameObject, on_delete=models.PROTECT, related_name="player_stats")
    # The player's all-time air-to-air Elo in this aircraft type (OQ-49): the highest of their per-tour final ratings in
    # it (`PlayerTourAircraft.elo`), games summed over tours. Written by `ingest.ratings.recompute_ratings`.
    elo = models.FloatField(default=1500.0)
    elo_games = models.PositiveIntegerField(default=0)

    class Meta(Counters.Meta):
        abstract = False
        constraints = [models.UniqueConstraint(fields=["player", "aircraft"], name="playeraircraft_unique")]

    def __str__(self) -> str:
        return f"{self.player_id} / {self.aircraft_id}"


class PlayerTour(Counters):
    """A player's counters within one tour (TD-26): the sum of their `PlayerMission` rows of that tour's missions.

    Rows exist only for players with a counted sortie in the tour. The tour's Elo is on `PlayerTourPool` and
    `PlayerTourAircraft`."""

    player_id: int
    tour_id: int

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="tour_rows")
    tour = models.ForeignKey(Tour, on_delete=models.CASCADE, related_name="player_rows")

    class Meta(Counters.Meta):
        abstract = False
        constraints = [models.UniqueConstraint(fields=["player", "tour"], name="playertour_unique")]

    def __str__(self) -> str:
        return f"{self.player_id} / tour {self.tour_id}"


class PlayerTourAircraft(Counters):
    """`PlayerAircraft` within one tour: counted sorties grouped by (player, tour, aircraft).

    A separate table rather than a nullable `tour` column on `PlayerAircraft`: all-time reads (`player.aircraft_stats`)
    stay unfiltered, and the unique key needs no NULL special case."""

    player_id: int
    tour_id: int
    aircraft_id: int

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="tour_aircraft_stats")
    tour = models.ForeignKey(Tour, on_delete=models.CASCADE, related_name="aircraft_rows")
    aircraft = models.ForeignKey(GameObject, on_delete=models.PROTECT, related_name="tour_player_stats")
    # The tour's final per-type Elo (OQ-49, OQ-128): everyone starts at `[ratings] start` at the tour start.
    elo = models.FloatField(default=1500.0)
    elo_games = models.PositiveIntegerField(default=0)

    class Meta(Counters.Meta):
        abstract = False
        constraints = [models.UniqueConstraint(fields=["player", "tour", "aircraft"], name="playertouraircraft_unique")]

    def __str__(self) -> str:
        return f"{self.player_id} / tour {self.tour_id} / {self.aircraft_id}"


class PlayerAircraftScope(Counters):
    """Level 2 (FR-WEB-8): one player's counters in one aircraft type within a scope of the aircraft page, for its top
    pilots: `tour` (null = all time), combat `role` (`AircraftRole`) and weapon-mod `mod_pattern` (see
    `TourAircraftStats`, which has the same scopes per type). Every scope has rows except the all-time, every role,
    unfiltered one, which is `PlayerAircraft` itself. Counted from the sorties in each scope, hidden players too
    (hiding is presentation only; the page leaves them out). `all` rows include sorties without a combat role."""

    player_id: int
    aircraft_id: int
    tour_id: int | None

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="aircraft_scopes")
    aircraft = models.ForeignKey(GameObject, on_delete=models.PROTECT, related_name="player_scopes")
    tour = models.ForeignKey(Tour, null=True, on_delete=models.CASCADE, related_name="+")
    role = models.CharField(max_length=16, choices=AircraftRole.choices, default=AircraftRole.ALL)
    mod_pattern = models.CharField(max_length=16, blank=True, default="")

    class Meta(Counters.Meta):
        abstract = False
        constraints = scoped_unique("playeraircraftscope", ["aircraft", "player", "role", "mod_pattern"])
        indexes = [models.Index(fields=["aircraft", "tour", "role", "mod_pattern"], name="aircraftscope_by_aircraft")]

    def __str__(self) -> str:
        return f"{self.player_id} / {self.aircraft_id} / {self.tour_id} / {self.role} / {self.mod_pattern or '*'}"


class BuildKind(models.TextChoices):
    PAYLOAD = "payload", "Loadout"


class PlayerAircraftBuild(models.Model):
    """Level 2 (FR-WEB-4, doc 16 "Aircraft stats" loadouts): what a player flies an aircraft type with, all time
    (`tour` NULL) or within one tour (same scoping as `PlayerAircraft` / `PlayerTourAircraft`).

    Counted sorties per loadout (`kind` `payload`, the only kind since OQ-117: `value` = `PlayerSortie.payload_id`,
    `label` = its name, '' when unknown). A nullable `tour` needs two partial unique constraints."""

    player_id: int
    aircraft_id: int
    tour_id: int | None

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="build_rows")
    aircraft = models.ForeignKey(GameObject, on_delete=models.PROTECT, related_name="player_build_rows")
    tour = models.ForeignKey(Tour, on_delete=models.CASCADE, null=True, related_name="build_rows")
    kind = models.CharField(max_length=8, choices=BuildKind.choices)
    value = models.BigIntegerField(default=0)
    label = models.CharField(max_length=128, blank=True)
    sorties = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["player", "aircraft", "kind", "value", "label"],
                condition=models.Q(tour__isnull=True),
                name="playeraircraftbuild_unique_all",
            ),
            models.UniqueConstraint(
                fields=["player", "aircraft", "tour", "kind", "value", "label"],
                condition=models.Q(tour__isnull=False),
                name="playeraircraftbuild_unique_tour",
            ),
        ]
        indexes = [models.Index(fields=["player", "tour"], name="playeraircraftbuild_by_player")]

    def __str__(self) -> str:
        return f"{self.player_id} / {self.aircraft_id} / {self.kind} {self.value} {self.label}"


class PlayerPool(Counters):
    """A player's all-time counters in one propulsion pool (`prop` / `jet`): counted sorties grouped by the aircraft's
    propulsion, so the leaderboards can be split into prop and jet without aggregating at request time (FR-WEB-7).
    Sorties in an aircraft of unknown propulsion are in no pool."""

    player_id: int

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="pool_rows")
    propulsion = models.CharField(max_length=4, choices=Propulsion.choices)

    class Meta(Counters.Meta):
        abstract = False
        constraints = [models.UniqueConstraint(fields=["player", "propulsion"], name="playerpool_unique")]
        indexes = [models.Index(fields=["propulsion"], name="playerpool_by_propulsion")]  # the pool boards

    def __str__(self) -> str:
        return f"{self.player_id} / {self.propulsion}"


class PlayerTourPool(Counters):
    """`PlayerPool` within one tour (TD-26): counted sorties grouped by (player, tour, propulsion)."""

    player_id: int
    tour_id: int

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="tour_pool_rows")
    tour = models.ForeignKey(Tour, on_delete=models.CASCADE, related_name="pool_rows")
    propulsion = models.CharField(max_length=4, choices=Propulsion.choices)
    # The tour's final air-to-air Elo in this pool (OQ-28, OQ-128): replayed from the tour's games alone, everyone
    # starting at `[ratings] start` at the tour start. `Player.elo_*` is the best of these.
    elo = models.FloatField(default=1500.0)
    elo_games = models.PositiveIntegerField(default=0)

    class Meta(Counters.Meta):
        abstract = False
        constraints = [models.UniqueConstraint(fields=["player", "tour", "propulsion"], name="playertourpool_unique")]

    def __str__(self) -> str:
        return f"{self.player_id} / tour {self.tour_id} / {self.propulsion}"


class PlayerRole(Counters):
    """A copy of the player's counters per combat role (maintainer 2026-10-05: "two additional copies of the player
    object, but with a role column"): one row per (player, tour, role) for the roles `air_superiority` and `attack`
    the pilot flew in that tour, counted from the tour's counted sorties of that role (like `PlayerTourAircraft`); the
    all-time rows (`tour` null) are the SUM of the player's tour rows (`ingest.rollup`). The every-role rows are
    `PlayerTour` / `Player` themselves, which the leaderboards keep reading. The profile's `?role=` view reads one
    row."""

    player_id: int
    tour_id: int | None

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="role_rows")
    tour = models.ForeignKey(Tour, null=True, on_delete=models.CASCADE, related_name="+")
    role = models.CharField(max_length=16, choices=CombatRole.choices)

    class Meta(Counters.Meta):
        abstract = False
        constraints = scoped_unique("playerrole", ["player", "role"])

    def __str__(self) -> str:
        return f"{self.player_id} / {self.tour_id} / {self.role}"


class AircraftAmmoStats(models.Model):
    """Level 2 (FR-WEB-18): hits to destroy per victim aircraft type and gun ammo, per scope of the aircraft page.

    The sum of `MissionAircraftAmmo` over the missions of the scope: `tour` null = all time, `role` (`AircraftRole`,
    `all` = every destroyed aircraft, AI included) and `mod_pattern` ('' = unfiltered, see `TourAircraftStats`) are
    those of the DESTROYED aircraft's sortie. The all-time `all` unfiltered rows are what the aircraft list reads. The
    average hits to destroy is `hits / kills`, computed when the page reads it (TD-22: no aggregation at request time).
    The `TOTAL_AMMO` row is all gun ammo together."""

    aircraft_id: int
    tour_id: int | None

    aircraft = models.ForeignKey(GameObject, on_delete=models.PROTECT, related_name="ammo_stats")
    tour = models.ForeignKey(Tour, null=True, on_delete=models.CASCADE, related_name="ammo_stats")
    role = models.CharField(max_length=16, choices=AircraftRole.choices, default=AircraftRole.ALL)
    mod_pattern = models.CharField(max_length=16, blank=True, default="")
    ammo = models.CharField(max_length=128)
    kills = models.PositiveIntegerField(default=0)
    hits = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = scoped_unique("aircraftammostats", ["aircraft", "role", "mod_pattern", "ammo"])

    def __str__(self) -> str:
        return f"{self.aircraft_id} / {self.ammo}"


class StatThreshold(models.Model):
    """Percentiles of one ratio over the pilots with at least `min_sorties` sorties (FR-WEB-22, `core.stat_marks`).

    Level 2: `ingest.stat_marks` recomputes them after the player rows, all-time (`tour` null) and per tour, and
    `rebuild-aggregates` rebuilds them. A scope without a row has too few pilots for a distribution. Hidden players are
    part of the population (hiding is presentation only, FR-ADM-3)."""

    tour_id: int | None
    tour = models.ForeignKey(Tour, null=True, on_delete=models.CASCADE, related_name="stat_thresholds")
    metric = models.CharField(max_length=24)  # a `core.stat_marks.Metric`
    min_sorties = models.PositiveIntegerField()  # the `[marks] min_sorties` these were computed with
    population = models.PositiveIntegerField()  # pilots with a defined value
    p10 = models.FloatField()
    p25 = models.FloatField()
    p50 = models.FloatField()
    p75 = models.FloatField()
    p90 = models.FloatField()
    p95 = models.FloatField(default=0.0)  # the Top 5% tier (2026-10-05); rewritten by the upgrade's threshold recompute
    p99 = models.FloatField(default=0.0)  # the Top 1% tier

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tour", "metric"], condition=models.Q(tour__isnull=False), name="statthreshold_tour_unique"
            ),
            models.UniqueConstraint(
                fields=["metric"], condition=models.Q(tour__isnull=True), name="statthreshold_alltime_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.metric} / {'all time' if self.tour_id is None else f'tour {self.tour_id}'}"


class SortieThreshold(models.Model):
    """Percentiles of one per-sortie number (air kills, ground kills; `core.stat_marks.SORTIE_METRICS`) over the counted
    pilot sorties of a tour (`tour` set) or of all time (null): the sortie page's marks (FR-WEB-22, 2026-10-05).

    Level 2: `ingest.stat_marks` rewrites the tours' rows from their sorties and the all-time row as the sum of the
    tours' `histogram`s (value -> sorties), so no history is read for it. Hidden players' sorties count (FR-ADM-3).
    No row = fewer than `MIN_POPULATION` sorties (as with `StatThreshold`); such a tour is not part of all time."""

    tour_id: int | None
    tour = models.ForeignKey(Tour, null=True, on_delete=models.CASCADE, related_name="sortie_thresholds")
    metric = models.CharField(max_length=16)  # a `core.stat_marks.SortieMetric`
    population = models.PositiveIntegerField()  # counted pilot sorties
    histogram: models.JSONField[dict[str, int]] = models.JSONField(
        default=dict
    )  # {"value": number of sorties}, JSON keys are strings
    p10 = models.FloatField(default=0.0)
    p25 = models.FloatField(default=0.0)
    p50 = models.FloatField(default=0.0)
    p75 = models.FloatField(default=0.0)
    p90 = models.FloatField(default=0.0)
    p95 = models.FloatField(default=0.0)
    p99 = models.FloatField(default=0.0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tour", "metric"], condition=models.Q(tour__isnull=False), name="sortiethreshold_tour_unique"
            ),
            models.UniqueConstraint(
                fields=["metric"], condition=models.Q(tour__isnull=True), name="sortiethreshold_alltime_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.metric} / {'all time' if self.tour_id is None else f'tour {self.tour_id}'}"


class AircraftCounters(Counters):
    """The counters of one aircraft type plus `pilots` and `side`: what `AircraftStats` (all time) and
    `TourAircraftStats` (one tour) share, so the aircraft pages read either one the same way."""

    aircraft_id: int

    pilots = models.PositiveIntegerField(default=0)
    side = models.CharField(max_length=8, blank=True, default="")

    class Meta(Counters.Meta):
        abstract = True


class AircraftStats(AircraftCounters):
    """Level 2 (FR-WEB-8): all-time counters per aircraft type, summed over every pilot's `PlayerAircraft` row.

    Hidden players are included: hiding is presentation only (FR-ADM-3). `pilots` = distinct players who flew the type.
    `side` is the side most of its sorties were flown for ('redfor', 'blufor' or ''). No ratio is stored (OQ-98): K/D,
    K/L, survival and the attack share are shown from the counters and sorted with `queries.sorting.Ratio`."""

    aircraft = models.OneToOneField(GameObject, on_delete=models.PROTECT, related_name="stats")
    # The aircraft type's own Elo (maintainer, 2026-10-05; `ingest.aircraft_stats`): the best qualifying tour's final
    # rating (`RatingRules.type_min_games`), the games of all tours summed. 1500 / 0 = no game (shown as a dash).
    elo = models.FloatField(default=1500.0)
    elo_games = models.PositiveIntegerField(default=0)

    class Meta(AircraftCounters.Meta):
        abstract = False

    def __str__(self) -> str:
        return f"aircraft {self.aircraft_id}"


class TourAircraftStats(AircraftCounters):
    """Level 2 (FR-WEB-8, TD-26): `AircraftStats` within one tour and/or one combat role.

    `tour` null = all time, but only for a role row or a modification-filter row: the all-time `all` row without a
    filter is `AircraftStats` itself (no duplicate).
    `mod_pattern` '' = unfiltered. A type with significant weapon modifications (`weapon_mods.csv`) also has a row per
    filter pattern: one character per significant mod in ascending id order, `*` any, `+` with it, `-` without it
    (`core.catalog.loader.mod_filter_patterns`). Those rows are all counted from the sorties (`all` too), 3**n - 1 more
    scopes per tour and role; types without significant mods have none.
    `role` `all` rows are the sum of the pilots' `PlayerTourAircraft` rows of that tour; `air_superiority` / `attack`
    rows come from the counted sorties of that combat role (`pilots` = distinct players who flew the type in that role).
    The nullable tour with conditional constraints is the pattern of `AircraftMatchup`; a sibling table for the all-time
    role rows would have split one read into two models. `side` is the side most of the row's sorties were flown for.
    Hidden players and missions count too (FR-ADM-3). Rows without a counted sortie are deleted."""

    tour_id: int | None

    aircraft = models.ForeignKey(GameObject, on_delete=models.PROTECT, related_name="tour_stats")
    tour = models.ForeignKey(Tour, null=True, on_delete=models.CASCADE, related_name="aircraft_stats")
    role = models.CharField(max_length=16, choices=AircraftRole.choices, default=AircraftRole.ALL)
    mod_pattern = models.CharField(max_length=16, blank=True, default="")
    # the row's counted sorties per side (a country that is neither: in neither): `side` is the larger one (ties: the
    # name that sorts first), so an all-time row's side is the argmax of the summed tour rows (doc 14)
    sorties_redfor = models.PositiveIntegerField(default=0)
    sorties_blufor = models.PositiveIntegerField(default=0)
    # The type's Elo within the tour (replayed from the tour's air superiority duels between types, a clean slate each
    # tour), on the unfiltered `all` and `air_superiority` role rows only (the Elo is air superiority by definition);
    # the all-time role row holds the best qualifying tour's. 1500 / 0 = no game.
    elo = models.FloatField(default=1500.0)
    elo_games = models.PositiveIntegerField(default=0)

    class Meta(AircraftCounters.Meta):
        abstract = False
        constraints = [
            models.UniqueConstraint(
                fields=["tour", "aircraft", "role", "mod_pattern"],
                condition=models.Q(tour__isnull=False),
                name="touraircraftstats_unique",
            ),
            models.UniqueConstraint(
                fields=["aircraft", "role", "mod_pattern"],
                condition=models.Q(tour__isnull=True),
                name="touraircraftstats_alltime_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(tour__isnull=False) | ~models.Q(role="all") | ~models.Q(mod_pattern=""),
                name="touraircraftstats_alltime_role",
            ),
        ]

    def __str__(self) -> str:
        scope = "all time" if self.tour_id is None else f"tour {self.tour_id}"
        return f"aircraft {self.aircraft_id} / {scope} / {self.role} / {self.mod_pattern or 'any mods'}"


class AircraftMatchup(models.Model):
    """Level 2 (FR-WEB-8): player-versus-player air kills of one aircraft type against another.

    `kills` = `Kill` rows (credit `kill`, enemy, pilot sorties on both sides) where a `killer_aircraft` shot down a
    `victim_aircraft` (the unscoped rows; the role / modification scopes repeat the kills of their side's sortie). A
    type's losses to another type are the reversed pair. Pairs without kills have no row.

    One row per scope: `tour` null = all time, else the kills of that tour's missions; `intercept` true = only kills
    where both sorties had the combat role air superiority (an intercept fight, doc 13), false = every kill. So
    every kill is counted in up to four rows (all/tour x all/intercept). Hidden players and missions count too
    (FR-ADM-3). Recomputed per (killer type, victim type) pair by `ingest.aircraft_stats`."""

    killer_aircraft_id: int
    victim_aircraft_id: int
    tour_id: int | None

    killer_aircraft = models.ForeignKey(GameObject, on_delete=models.PROTECT, related_name="matchup_kills")
    victim_aircraft = models.ForeignKey(GameObject, on_delete=models.PROTECT, related_name="matchup_losses")
    tour = models.ForeignKey(Tour, null=True, on_delete=models.CASCADE, related_name="aircraft_matchups")
    intercept = models.BooleanField(default=False)
    # The role / modification scope of one side's sortie: `scoped_side` '' = unscoped (every kill, `combat_role` `all`
    # and no pattern); 'killer' or 'victim' = only the kills where that side's sortie had `combat_role` (`AircraftRole`,
    # never `all` with an empty pattern) and weapon mods matching `mod_pattern` (of that side's type, see
    # `TourAircraftStats.mod_pattern`). The page of type A reads its kills as the killer side and its losses as the
    # victim side of its own scope.
    scoped_side = models.CharField(max_length=6, blank=True, default="")
    combat_role = models.CharField(max_length=16, default=AircraftRole.ALL)
    mod_pattern = models.CharField(max_length=16, blank=True, default="")
    kills = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = scoped_unique(
            "aircraftmatchup",
            ["killer_aircraft", "victim_aircraft", "intercept", "scoped_side", "combat_role", "mod_pattern"],
        )
        indexes = [models.Index(fields=["killer_aircraft", "tour", "intercept"], name="matchup_by_killer")]

    def __str__(self) -> str:
        return f"{self.killer_aircraft_id} -> {self.victim_aircraft_id}"


class AircraftEffectiveness(models.Model):
    """The columns every "effectiveness by X" table of the aircraft page shares (doc 13): counted sorties of a type in
    one group (a loadout, a weapon-mod set) in one tour (`tour` null = all time), the combat role of the sorties and the
    modification filter scope
    (`TourAircraftStats.mod_pattern`; '' = unfiltered), with what the effectiveness columns are made of."""

    aircraft_id: int

    tour_id: int | None

    tour = models.ForeignKey(Tour, null=True, on_delete=models.CASCADE, related_name="+")  # null = all time
    combat_role = models.CharField(max_length=16, blank=True, default="")  # of its sorties; '' = none recorded
    mod_pattern = models.CharField(max_length=16, blank=True, default="")
    sorties = models.PositiveIntegerField(default=0)
    kills_air = models.PositiveIntegerField(default=0)
    kills_ground = models.PositiveIntegerField(default=0)
    deaths = models.PositiveIntegerField(default=0)
    # PvP air kills for K/D, the flight time and attack score and time on target for the per-hour rates the
    # leaderboards use.
    kills_air_pvp = models.PositiveIntegerField(default=0)
    flight_time_s = models.FloatField(default=0.0)
    score_ground_attack = models.FloatField(default=0.0)
    time_on_target_s = models.FloatField(default=0.0)
    # Sortie-weighted average Elo of the pilots who flew it (air superiority groups; per-type Elo where the pilot has
    # games in the type, else the propulsion pool's). Order-dependent like every Elo: written by
    # `ingest.aircraft_stats.recompute_payload_elo` after `recompute_ratings`. Null = no rated pilot.
    elo_avg = models.FloatField(null=True, blank=True)

    class Meta:
        abstract = True


class AircraftPayload(AircraftEffectiveness):
    """Level 2 (FR-WEB-8): counted sorties per aircraft type, loadout (`PlayerSortie.payload_name`; '' = unnamed),
    combat role of the sortie (a loadout has one role in practice: bombs, rockets and napalm make a sortie `attack`) and
    modification filter."""

    aircraft = models.ForeignKey(GameObject, on_delete=models.PROTECT, related_name="payload_stats")
    payload_name = models.CharField(max_length=128, blank=True)

    class Meta(AircraftEffectiveness.Meta):
        abstract = False
        constraints = scoped_unique("aircraftpayload", ["aircraft", "payload_name", "combat_role", "mod_pattern"])

    def __str__(self) -> str:
        return f"{self.aircraft_id} / {self.payload_name}"


class AircraftMods(AircraftEffectiveness):
    """Level 2 (FR-WEB-8): counted sorties per aircraft type, weapon-modification set (`PlayerSortie.weapon_mods`, the
    WM bitmask as flown, base bit included), combat role and modification filter: the "Mods" table of the aircraft page,
    next to `AircraftPayload`. The names come from `weapon_mods.csv` at read time."""

    aircraft = models.ForeignKey(GameObject, on_delete=models.PROTECT, related_name="mods_stats")
    weapon_mods = models.IntegerField(default=0)

    class Meta(AircraftEffectiveness.Meta):
        abstract = False
        constraints = scoped_unique("aircraftmods", ["aircraft", "weapon_mods", "combat_role", "mod_pattern"])

    def __str__(self) -> str:
        return f"{self.aircraft_id} / mods {self.weapon_mods}"


class PlayerKillboard(models.Model):
    """The killboard (FR-WEB-9): how often `player` and `opponent` shot each other down in PvP air combat.

    Every pair has two mirror rows, one per perspective, so a player's board is one indexed read: `kills` = times
    `player` got the kill credit on `opponent`, `deaths` = times `opponent` got it on `player`. Only kill credits count
    (not friendly fire) between two pilot sorties of different accounts; assists count only in `assists` and only with
    `[killboard] assists` on (otherwise 0): `assists` = times `player` got an assist credit on a sortie of `opponent`,
    `assists_received` = times `opponent` got one on a sortie of `player` (OQ-81: shown as a detail, not a column).
    `last_at` / `last_mission` = the latest such kill (or assist, when counted) in either direction. A pair with only
    assists has `kills = deaths = 0`. Level 2: recomputed per affected player by `ingest.pairs`."""

    player_id: int
    opponent_id: int
    last_mission_id: int

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="killboard_rows")
    opponent = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="+")
    kills = models.PositiveIntegerField(default=0)
    deaths = models.PositiveIntegerField(default=0)
    assists = models.PositiveIntegerField(default=0)
    assists_received = models.PositiveIntegerField(default=0)
    last_at = models.DateTimeField()
    last_mission = models.ForeignKey(Mission, on_delete=models.CASCADE, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["player", "opponent"], name="playerkillboard_unique")]
        indexes = [
            models.Index(fields=["player", "-kills"], name="killboard_by_kills"),
            models.Index(fields=["player", "-deaths"], name="killboard_by_deaths"),
        ]

    def __str__(self) -> str:
        return f"{self.player_id} vs {self.opponent_id}"


class PlayerTourKillboard(models.Model):
    """`PlayerKillboard` within one tour (the kills of that tour's missions only). Same meaning and mirror rows; a
    separate table so the all-time reads stay as they are (like `PlayerTourAircraft`)."""

    player_id: int
    opponent_id: int
    tour_id: int
    last_mission_id: int

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="tour_killboard_rows")
    opponent = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="+")
    tour = models.ForeignKey(Tour, on_delete=models.CASCADE, related_name="killboard_rows")
    kills = models.PositiveIntegerField(default=0)
    deaths = models.PositiveIntegerField(default=0)
    assists = models.PositiveIntegerField(default=0)
    assists_received = models.PositiveIntegerField(default=0)
    last_at = models.DateTimeField()
    last_mission = models.ForeignKey(Mission, on_delete=models.CASCADE, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["player", "tour", "opponent"], name="playertourkillboard_unique")
        ]
        indexes = [
            models.Index(fields=["player", "tour", "-kills"], name="tourkillboard_by_kills"),
            models.Index(fields=["player", "tour", "-deaths"], name="tourkillboard_by_deaths"),
        ]

    def __str__(self) -> str:
        return f"{self.player_id} vs {self.opponent_id} / tour {self.tour_id}"


class PlayerTypeKillboard(models.Model):
    """The killboard by aircraft type (FR-WEB-9): how often `player` shot down, and was shot down by, one enemy type.

    One row per (player, scope, enemy type); `tour` null = all time, else the kills of that tour's missions. `kills` =
    kill credits of the player on sorties flying `enemy_aircraft`, `deaths` = kill credits of pilots flying
    `enemy_aircraft` on the player's sorties; same rules as `PlayerKillboard` (enemy PvP air kills between pilot
    sorties of two accounts, no friendly fire, assists never counted). `kills_with` / `deaths_in` = the player's own
    aircraft type most used in those kills / deaths (ties: the lowest id), null while there are none. Hidden opponents
    count, no name is shown (FR-ADM-3). Level 2: `ingest.type_board`, recomputed per affected player."""

    player_id: int
    enemy_aircraft_id: int
    tour_id: int | None
    kills_with_id: int | None
    deaths_in_id: int | None

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="type_killboard_rows")
    tour = models.ForeignKey(Tour, null=True, on_delete=models.CASCADE, related_name="type_killboard_rows")
    enemy_aircraft = models.ForeignKey(GameObject, on_delete=models.PROTECT, related_name="+")
    kills = models.PositiveIntegerField(default=0)
    deaths = models.PositiveIntegerField(default=0)
    kills_with = models.ForeignKey(GameObject, null=True, on_delete=models.PROTECT, related_name="+")
    deaths_in = models.ForeignKey(GameObject, null=True, on_delete=models.PROTECT, related_name="+")
    # Per-tour rows only: the player's own aircraft type id -> kills / deaths with it in that tour. The all-time
    # `kills_with` / `deaths_in` are the most used type of the summed counts, which no per-tour winner can give.
    kills_with_counts: models.JSONField[dict[str, int]] = models.JSONField(default=dict)
    deaths_in_counts: models.JSONField[dict[str, int]] = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["player", "tour", "enemy_aircraft"],
                condition=models.Q(tour__isnull=False),
                name="playertypekillboard_tour_unique",
            ),
            models.UniqueConstraint(
                fields=["player", "enemy_aircraft"],
                condition=models.Q(tour__isnull=True),
                name="playertypekillboard_alltime_unique",
            ),
        ]
        indexes = [models.Index(fields=["player", "tour"], name="typekillboard_by_player")]

    def __str__(self) -> str:
        return f"{self.player_id} vs type {self.enemy_aircraft_id}"


class StreakKind(models.TextChoices):
    SORTIES = "sorties"
    AIR_KILLS = "air_kills"
    FLIGHT_TIME = "flight_time"
    GROUND_KILLS = "ground_kills"  # the ground track's kills criterion (`air_kills` is the air track's)
    KILLS = "kills"  # the all track's kills criterion: air plus ground kills


class StreakTrack(models.TextChoices):
    """The three ironman tracks (maintainer, 2026-10-05): `all` takes every pilot sortie (any death or capture ends it),
    attack sorties are the ground track, the rest the air track. Values match `core.streaks.Track`; `all` first (the
    order every page shows them in)."""

    ALL = "all"
    AIR = "air"
    GROUND = "ground"


class PlayerBestStreak(models.Model):
    """A player's best ironman streak by one criterion (FR-WEB-23): the run with the most survived sorties, the most
    air kills, or the most flight time. `tour` null = all time, the best over the tour rows (a streak never spans
    two tours); with a tour, the streak runs within that tour's sorties only. `since` / `until` as in
    `core.streaks.Streak`. A kills row (`air_kills` on the air track, `ground_kills` on the ground track) exists only
    when the best such streak has at least one kill. `track` is the ironman track (all, air or ground, maintainer
    2026-10-05): the streak counts the sorties of that track only and carries both kill counts. Level 2:
    `ingest.streaks`."""

    player_id: int
    tour_id: int | None

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="best_streaks")
    tour = models.ForeignKey(Tour, null=True, on_delete=models.CASCADE, related_name="best_streaks")
    track = models.CharField(max_length=6, choices=StreakTrack.choices, default=StreakTrack.AIR)
    kind = models.CharField(max_length=12, choices=StreakKind.choices)
    sorties = models.PositiveIntegerField(default=0)
    kills_air = models.PositiveIntegerField(default=0)
    kills_ground = models.PositiveIntegerField(default=0)
    flight_time_s = models.FloatField(default=0.0)
    since = models.DateTimeField()
    until = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["player", "tour", "track", "kind"],
                condition=models.Q(tour__isnull=False),
                name="bests_tour_track_unique",
            ),
            models.UniqueConstraint(
                fields=["player", "track", "kind"],
                condition=models.Q(tour__isnull=True),
                name="bests_alltime_track_unique",
            ),
            models.CheckConstraint(condition=models.Q(kind__in=StreakKind.values), name="bests_kind_valid"),
            models.CheckConstraint(condition=models.Q(track__in=StreakTrack.values), name="bests_track_valid"),
        ]
        indexes = [
            models.Index(fields=["player", "tour"], name="bests_by_player_tour"),
            # The ironman board: one track and kind in one tour (or all time), longest first.
            models.Index(fields=["track", "kind", "tour", "-sorties", "-kills_air"], name="bests_track_list"),
            # The all-time list: Postgres cannot read `tour IS NULL` as a fixed prefix of the one above, so it sorts.
            models.Index(
                fields=["track", "kind", "-sorties", "-kills_air", "id"],
                condition=models.Q(tour__isnull=True),
                name="bests_alltime_track_list",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.player_id} {self.kind}: {self.sorties}"


class StreakEnd(models.TextChoices):
    DEATH = "death"
    CAPTURED = "captured"
    OPEN = "open"


class PlayerStreakRun(models.Model):
    """One ironman streak of a player (FR-WEB-25, OQ-82): a run of at least `core.streaks.MIN_LISTED_RUN` survived
    sorties, finished or still going, with what ended it. `tour` null = all time, else the run within that tour's
    sorties only. `ended_sortie` = the fatal or capturing sortie (null while `ended_by` is open). `track` = the ironman
    track (all, air or ground). Level 2: recomputed per affected player by `ingest.streaks`."""

    player_id: int
    tour_id: int | None
    ended_sortie_id: int | None

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="streak_runs")
    tour = models.ForeignKey(Tour, null=True, on_delete=models.CASCADE, related_name="streak_runs")
    track = models.CharField(max_length=6, choices=StreakTrack.choices, default=StreakTrack.AIR)
    sorties = models.PositiveIntegerField(default=0)
    kills_air = models.PositiveIntegerField(default=0)
    kills_ground = models.PositiveIntegerField(default=0)
    flight_time_s = models.FloatField(default=0.0)
    since = models.DateTimeField()
    until = models.DateTimeField()
    ended_by = models.CharField(max_length=8, choices=StreakEnd.choices)
    ended_sortie = models.ForeignKey(PlayerSortie, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        indexes = [
            models.Index(fields=["player", "tour", "track", "-since"], name="streakruns_track_player_tour"),
            # The all-time history (`tour IS NULL`), newest first: see `bests_alltime_track_list` on PlayerBestStreak.
            models.Index(
                fields=["player", "track", "-since", "-id"],
                condition=models.Q(tour__isnull=True),
                name="streakruns_track_alltime",
            ),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(ended_by__in=StreakEnd.values), name="runs_end_valid"),
            models.CheckConstraint(condition=models.Q(track__in=StreakTrack.values), name="runs_track_valid"),
        ]

    def __str__(self) -> str:
        return f"{self.player_id}: {self.sorties} sorties ({self.ended_by})"


class PlayerStreak(models.Model):
    """Ironman streaks (FR-WEB-23): current and best run of survived sorties, rule in `il2ks.core.streaks`.

    One row per player and track (`track`: all, air or ground, maintainer 2026-10-05); a row exists for players with at
    least one survived sortie on the track. `best_*` is the best run over all tours (a streak never
    spans two tours); `current_*` is the run in the current (newest) tour, zero for a player who has not flown in it
    (`current_tour` = that tour, null when the player has not). Level 2: recomputed per affected player by
    `ingest.streaks`."""

    player_id: int
    current_tour_id: int | None

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="streaks")
    track = models.CharField(max_length=6, choices=StreakTrack.choices, default=StreakTrack.AIR)
    current_tour = models.ForeignKey(Tour, null=True, on_delete=models.SET_NULL, related_name="+")
    current_sorties = models.PositiveIntegerField(default=0)
    current_kills_air = models.PositiveIntegerField(default=0)
    current_kills_ground = models.PositiveIntegerField(default=0)
    current_flight_time_s = models.FloatField(default=0.0)
    current_since = models.DateTimeField(null=True)
    current_until = models.DateTimeField(null=True)
    best_sorties = models.PositiveIntegerField(default=0)
    best_kills_air = models.PositiveIntegerField(default=0)
    best_kills_ground = models.PositiveIntegerField(default=0)
    best_flight_time_s = models.FloatField(default=0.0)
    best_since = models.DateTimeField(null=True)
    best_until = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["player", "track"], name="streak_player_track_unique"),
            models.CheckConstraint(condition=models.Q(track__in=StreakTrack.values), name="streak_track_valid"),
        ]
        indexes = [models.Index(fields=["track", "-current_sorties"], name="streak_track_current_desc")]

    def __str__(self) -> str:
        return f"{self.player_id}: {self.current_sorties} / {self.best_sorties}"


class PlayerAchievement(models.Model):
    """A medal or ribbon tier a pilot has earned (FR-WEB-26, doc 17): level 2, recomputed per affected player by
    `ingest.achievements` from the rules in `il2ks.core.achievements`.

    One row per earned tier (a pilot at tier 3 also has the rows of tiers 1 and 2), with the sortie that first reached
    it. `tour` null = all time; with a tour, the same definitions ran over that tour's sorties only (a life, a streak, a
    run of weeks starts fresh in a tour), so a tier can be earned again in every tour. Hidden players keep their rows;
    the pages leave them out (FR-ADM-3)."""

    player_id: int
    sortie_id: int
    mission_id: int
    tour_id: int | None

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="achievements")
    tour = models.ForeignKey(Tour, null=True, on_delete=models.CASCADE, related_name="achievements")
    key = models.CharField(max_length=32)
    tier = models.PositiveSmallIntegerField()
    earned_at = models.DateTimeField()
    sortie = models.ForeignKey(PlayerSortie, on_delete=models.CASCADE, related_name="achievements")
    mission = models.ForeignKey(Mission, on_delete=models.CASCADE, related_name="achievements")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["player", "tour", "key", "tier"],
                condition=models.Q(tour__isnull=False),
                name="achievement_tour_unique",
            ),
            models.UniqueConstraint(
                fields=["player", "key", "tier"], condition=models.Q(tour__isnull=True), name="achievement_unique"
            ),
        ]
        indexes = [
            models.Index(fields=["tour", "key", "tier", "-earned_at"], name="achievement_holders_tour"),
            models.Index(fields=["tour", "-earned_at"], name="achievement_feed"),
        ]

    def __str__(self) -> str:
        return f"{self.player_id} {self.key} {self.tier}"


class AchievementHolders(models.Model):
    """How many visible pilots hold a medal tier in a scope, and out of how many (the overview page and the rarity
    shown on every medal; TD-22: no counting at request time). `tour` null = all time.

    Level 2, rewritten whole by `ingest.achievements.recompute_holders` after the player rows, and when an admin hides
    or shows a player. A row exists per tier somebody holds. `pilots` is the scope's denominator, the same on every row
    of a scope: visible players with at least one counted pilot sortie in it (`[PROPOSED]`, doc 17); the rarity is
    `holders / pilots`. 0 on rows from before it existed (until the upgrade backfill rewrites them)."""

    tour_id: int | None

    tour = models.ForeignKey(Tour, null=True, on_delete=models.CASCADE, related_name="achievement_holders")
    key = models.CharField(max_length=32)
    tier = models.PositiveSmallIntegerField()
    holders = models.PositiveIntegerField(default=0)
    pilots = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tour", "key", "tier"],
                condition=models.Q(tour__isnull=False),
                name="achievement_holders_tour_unique",
            ),
            models.UniqueConstraint(
                fields=["key", "tier"], condition=models.Q(tour__isnull=True), name="achievement_holders_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.key} {self.tier}: {self.holders}"


# --- Level 2: server activity ---


class ActivityDay(models.Model):
    """Server activity per UTC calendar day (FR-WEB-16, the home page's activity chart): level 2, written by `ingest`.

    Built from the day's visible (not hidden) missions, by their start time: how many missions, their sorties and air
    kills, and the distinct pilots (counted roles) who flew in them. Hidden players still count as a pilot, like in a
    mission's `players_total`. Rows exist only for days with a visible mission. Hiding a mission refreshes its day."""

    day = models.DateField(unique=True)
    missions = models.PositiveIntegerField(default=0)
    sorties = models.PositiveIntegerField(default=0)
    pilots = models.PositiveIntegerField(default=0)
    kills_air = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        return str(self.day)


# --- Operational ---


class IngestStatus(models.TextChoices):
    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"


class CompletionReason(models.TextChoices):
    """Why a mission counted as complete when it was ingested (FR-ING-2, FR-ING-18)."""

    MISSION_END = "mission_end"  # AType 7 seen, and the settle time passed
    NEWER_MISSION = "newer_mission"  # a newer mission's [0] part exists
    IDLE = "idle"  # no new part for the idle timeout: admins review these
    IMPORT = "import"  # `ingest --from` or a whole-mission archive in the log folder
    REPROCESS = "reprocess"  # rebuilt from the stored archive


class IngestRun(models.Model):
    """One attempt to ingest one mission (FR-ING-11, FR-ING-18, FR-ING-19)."""

    mission_id: int | None

    mission_uid = models.CharField(max_length=32, db_index=True)
    mission = models.ForeignKey(Mission, on_delete=models.SET_NULL, null=True, related_name="ingest_runs")
    files: models.JSONField[list[str]] = models.JSONField(default=list)  # source file names
    fingerprint = models.CharField(max_length=64)  # sha256 over (name, size, mtime) of the source files
    archive_path = models.CharField(max_length=500, blank=True)
    archive_sha256 = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=8, choices=IngestStatus.choices)
    completion_reason = models.CharField(max_length=16, choices=CompletionReason.choices, blank=True, default="")
    attempts = models.PositiveIntegerField(default=1)
    next_retry_at = models.DateTimeField(null=True)
    il2ks_version = models.CharField(max_length=32, blank=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True)
    lines_total = models.PositiveIntegerField(default=0)
    lines_bad = models.PositiveIntegerField(default=0)
    log_version = models.IntegerField(null=True)
    unknown_atypes: models.JSONField[dict[str, int]] = models.JSONField(default=dict)
    unknown_keys: models.JSONField[dict[str, int]] = models.JSONField(default=dict)
    warnings: models.JSONField[list[str]] = models.JSONField(default=list)
    error = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(status__in=IngestStatus.values), name="ingestrun_status_valid"),
            models.CheckConstraint(
                condition=models.Q(completion_reason__in=["", *CompletionReason.values]),
                name="ingestrun_completion_reason_valid",
            ),
        ]
        indexes = [models.Index(fields=["mission_uid", "-started_at"], name="ingestrun_latest")]

    def __str__(self) -> str:
        return f"{self.mission_uid} {self.status}"


class ReprocessStatus(models.TextChoices):
    PENDING = "pending"  # asked for in the admin; `watch` starts it at its next tick
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"  # the job itself crashed (individual failed missions still end as `done`, with counts)


class ReprocessRequest(models.Model):
    """An admin's request to reprocess missions (doc 14). The web process only writes this row; the `watch` loop
    (also run by `il2ks run`) picks it up at its next tick and runs the reprocess under the writer lock.

    `since` and `until` both empty means every mission. At most one request is pending at a time."""

    requested_at = models.DateTimeField()
    requested_by = models.CharField(max_length=150, blank=True)
    since = models.DateField(null=True)  # server-local mission dates, inclusive (as `il2ks reprocess --since`)
    until = models.DateField(null=True)
    status = models.CharField(max_length=8, choices=ReprocessStatus.choices, default=ReprocessStatus.PENDING)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    missions_total = models.PositiveIntegerField(default=0)  # known once it starts
    missions_ok = models.PositiveIntegerField(default=0)
    missions_failed = models.PositiveIntegerField(default=0)
    missions_missing = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=ReprocessStatus.values), name="reprocessrequest_status_valid"
            ),
            models.UniqueConstraint(
                fields=["status"],
                condition=models.Q(status="pending"),
                name="reprocessrequest_one_pending",
            ),
        ]
        ordering = ["-requested_at", "-pk"]

    def __str__(self) -> str:
        return f"reprocess {self.status} ({self.requested_at:%Y-%m-%d %H:%M})"

    @property
    def is_all(self) -> bool:
        return self.since is None and self.until is None


class RedforEmblem(models.TextChoices):
    """Doc 15: neutral by default; period insignia are optional (drawn as simple shapes, free of copyright)."""

    NEUTRAL = "neutral"
    VVS = "vvs", "Soviet VVS"
    PLAAF = "plaaf", "Chinese PLAAF"
    KPAF = "kpaf", "North Korean KPAF"


class BluforEmblem(models.TextChoices):
    NEUTRAL = "neutral"
    USAF = "usaf", "US star-and-bar"
    ROKAF = "rokaf", "South Korean ROKAF"
    UN = "un", "UN-style roundel"


class HomeFeature(models.TextChoices):
    """What dominates the front page (`SiteSettings.home_feature`). `embed` (an iframe) may come later."""

    NONE = "none", "Nothing (the normal front page)"
    IMAGE = "image", "A large image from a file on the server"


class SiteSettings(models.Model):
    """Branding and site texts, edited in the admin (FR-ADM-2, TD-25). A singleton: always pk=1 (`il2ks.db.site`)."""

    site_title = models.CharField(max_length=100, default="IL-2 Korea stats")
    server_name = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)  # shown on the home page
    # Path relative to MEDIA_ROOT of the re-encoded logo (raster only, never SVG; FR-ADM-2). Empty = no logo.
    logo = models.CharField(max_length=200, blank=True)
    # Colour overrides by token: {"light": {"accent": "#A86A14", ...}, "dark": {...}}; empty = the default look. The
    # token list and validation live in `il2ks.web.theme` (TD-25); readers sanitize again (no CSS injection).
    theme: models.JSONField[dict[str, dict[str, str]]] = models.JSONField(default=dict, blank=True)
    # Font choices by key (`il2ks.web.theme.HEADING_FONTS` / `BODY_FONTS`); empty = the default font.
    heading_font = models.CharField(max_length=20, blank=True)
    body_font = models.CharField(max_length=20, blank=True)
    # Uploaded fonts: [{"file": "branding/font-<hash16>.woff2", "label": "..."}], at most `web.fonts.MAX_CUSTOM_FONTS`.
    # A font is selected by putting its key (`up-<hash8>`) into heading_font / body_font. Readers re-validate
    # (`web.fonts.clean_fonts`), so a hand-edited row cannot inject CSS.
    custom_fonts: models.JSONField[list[dict[str, str]]] = models.JSONField(default=list, blank=True)
    # The custom navigation links, in order: [{"label", "url", "icon"}]. A published copy of the `NavLink` rows, written
    # by the admin on save, so pages read them with the settings row and need no extra query (page budgets).
    links: models.JSONField[list[dict[str, str]]] = models.JSONField(default=list, blank=True)
    # Front-page feature (FR-ADM-2): `none` keeps the home page as it is; `image` shows a large image (e.g. a map of
    # the current situation) read from a file on the server. An `embed` mode (an iframe) can be added later.
    home_feature = models.CharField(max_length=10, choices=HomeFeature.choices, default=HomeFeature.NONE)
    feature_image_path = models.CharField(max_length=500, blank=True)  # the configured file; never served itself
    feature_caption = models.CharField(max_length=200, blank=True)
    feature_alt = models.CharField(max_length=300, blank=True)
    # Output of `web.feature_image.sync` (the admin save, and a polling thread of the web process): the re-encoded
    # copies under MEDIA_ROOT (names relative to it, content-hashed), their size, the source file's mtime,
    # the signature of the
    # last file looked at (path, mtime, size: unchanged = nothing to do) and why the last attempt failed ('' = fine).
    feature_image = models.CharField(max_length=200, blank=True)
    feature_image_small = models.CharField(max_length=200, blank=True)
    feature_image_width = models.PositiveIntegerField(default=0)
    feature_image_height = models.PositiveIntegerField(default=0)
    feature_image_updated = models.DateTimeField(null=True, blank=True)
    feature_source_sig = models.CharField(max_length=700, blank=True)
    feature_error = models.CharField(max_length=300, blank=True)
    # Coalition display names (FR-ADM-5, doc 06): 5xx countries are REDFOR, 6xx BLUFOR.
    redfor_name = models.CharField(max_length=40, default="REDFOR")
    blufor_name = models.CharField(max_length=40, default="BLUFOR")
    redfor_emblem = models.CharField(max_length=10, choices=RedforEmblem.choices, default=RedforEmblem.NEUTRAL)
    blufor_emblem = models.CharField(max_length=10, choices=BluforEmblem.choices, default=BluforEmblem.NEUTRAL)
    # Not branding: the `[killboard] assists` setting the level-2 rows were last rebuilt with (`ingest.aggregates`,
    # `rebuild-aggregates`), so the pages can show the assists column without reading the config file.
    killboard_assists = models.BooleanField(default=False)
    # Flavor text (FR-WEB-23, admin-configurable quips): the global switch, and the per-spot choices as one JSON object
    # `{"modes": {spot: mode}, "hidden": {spot: [english default text, ...]}, "custom": [{"spot", "text", "language",
    # "enabled"}]}` (parsed and validated by `il2ks.web.quips`; empty = every default quip on). Kept on the settings
    # row, which every page reads already, so a quip costs no extra query.
    quips_enabled = models.BooleanField(default=True)
    quips: models.JSONField[dict[str, object]] = models.JSONField(default=dict, blank=True)
    # Admin-configurable achievements: `achievements` is the admin's choice `{"off": [key], "thresholds": {key: [n]},
    # "names": {key: {language: text}}, "descriptions": {...}}` (`il2ks.web.achievement_config`; empty = the built-in
    # set, unchanged). `achievements_applied` is what the stored `PlayerAchievement` rows were last computed with
    # (`{"off", "thresholds"}`, written only by `ingest.achievements`); the two differ while a recompute is pending.
    # Both live on the settings row every page reads already, so they cost no query.
    achievements: models.JSONField[dict[str, object]] = models.JSONField(default=dict, blank=True)
    achievements_applied: models.JSONField[dict[str, object]] = models.JSONField(default=dict, blank=True)
    # Optional flight-time points (maintainer request, 2026-10-05): `score_flight` is the admin's choice
    # `{"enabled": bool, "per_hour": float}` (`core.ratings.score.FlightScore`; empty = off, the default rate),
    # `score_flight_applied` what the stored sortie scores were last computed with (written only by
    # `ingest.flight_score`); the two differ while a re-score is pending (`watch` or `rebuild-aggregates` applies it).
    score_flight: models.JSONField[dict[str, object]] = models.JSONField(default=dict, blank=True)
    score_flight_applied: models.JSONField[dict[str, object]] = models.JSONField(default=dict, blank=True)
    # The game rules of `il2ks.toml` that the admin overrides (maintainer decision 2026-10-05; `il2ks.rule_settings`): a
    # flat JSON object `{"score.air_kill_pvp": 12.0, "tours.mode": "manual", ...}`. `rule_settings` is what the admin
    # chose, `rule_settings_applied` what the stored numbers were last computed with (the pair of the achievements): the
    # rules that change stored numbers (scoring, ratings, assists, tours) stay pending until `watch` or
    # `rebuild-aggregates` applied them; display-only ones and the replay rules (new missions only) are written to both
    # at once. A key that is absent uses `il2ks.toml`, or the built-in default.
    rule_settings: models.JSONField[dict[str, object]] = models.JSONField(default=dict, blank=True)
    rule_settings_applied: models.JSONField[dict[str, object]] = models.JSONField(default=dict, blank=True)
    # "Show sorties of the running mission" (FR-ING-15): `watch` saves the running mission provisionally every few
    # minutes, so its sorties show on the pages and move the counters before the mission ends. Off = online now only.
    show_live_sorties = models.BooleanField(default=True)
    # "Start a new tour when a mission is won by one side" (Tours admin page; default off). `tour_on_win` is what the
    # admin chose; `tour_on_win_applied` is what the stored tours were last assigned with (written only by
    # `ingest.tours`: a retour). They differ while a re-assignment is pending, like the achievements' pair.
    tour_on_win = models.BooleanField(default=False)
    tour_on_win_applied = models.BooleanField(default=False)
    # Not branding either: the one-time upgrade backfills that already ran (`ops.migrate`), so a trigger that is also
    # true on a healthy database (e.g. every score 0 under percentage penalties) can't rebuild after every migration.
    backfills_done: models.JSONField[list[str]] = models.JSONField(default=list, blank=True)
    # A batched level-2 run (`ingest.batch`) is under way: `{"since": ISO time, "command": "ingest", "pid": N}`, empty =
    # none. Set when the batch starts, cleared when its last level-2 pass or any rebuild committed. Still set at
    # the next run = killed mid-batch (level 2 lags level 1): that run rebuilds first, and `doctor` warns.
    level2_pending: models.JSONField[dict[str, object]] = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "site settings"
        verbose_name_plural = "site settings"

    def __str__(self) -> str:
        return self.site_title


class NavIcon(models.TextChoices):
    """The small built-in icons for a custom navigation link (static/il2ks/img/nav/<value>.svg)."""

    NONE = "", "No icon"
    DISCORD = "discord", "Discord"
    FORUM = "forum", "Forum"
    PATREON = "patreon", "Patreon"
    YOUTUBE = "youtube", "YouTube"
    TWITCH = "twitch", "Twitch"
    GITHUB = "github", "GitHub"
    TELEGRAM = "telegram", "Telegram"
    STEAM = "steam", "Steam"
    MAIL = "mail", "E-mail"
    BOOK = "book", "Rules / wiki"
    LINK = "link", "Generic link"


class NavLink(models.Model):
    """One extra link in the site's top navigation, after the built-in ones (TD-25). Edited as an ordered inline of
    `SiteSettings`; the admin publishes the list into `SiteSettings.links` when it saves."""

    site = models.ForeignKey(SiteSettings, on_delete=models.CASCADE, related_name="nav_links")
    label = models.CharField(max_length=60)
    url = models.CharField(max_length=NAV_URL_MAX_LENGTH, validators=[validate_http_url])
    icon = models.CharField(max_length=10, choices=NavIcon.choices, blank=True, default=NavIcon.NONE)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self) -> str:
        return self.label


class DataVersion(models.Model):
    """Bumped whenever page-visible data changes (TD-28): ETags are built from it. A singleton: always pk=1."""

    version = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return str(self.version)


# --- Online now (FR-ING-12): the running mission as `watch` last saw it. Outside the data version (TD-28). ---


class LiveState(models.TextChoices):
    """Values match `core.replay.live.LiveState`."""

    IN_FLIGHT = "in_flight"
    ON_GROUND = "on_ground"
    SPAWNED = "spawned"
    CONNECTED = "connected"  # online without an open sortie


class LiveMission(models.Model):
    """The in-progress mission of one server, overwritten by every live snapshot (`ingest.live`). One row per server.

    Not page data in the TD-28 sense: writing it never bumps `DataVersion`; the page that shows it (`/live/`) carries
    its own short max-age. `is_running` turns False when the mission completes (the normal ingest takes over), keeping
    `updated_at` as "last seen". A row whose `updated_at` is older than 3 x `interval_s` is stale: `watch` isn't
    running."""

    server_uid = models.UUIDField(unique=True)
    mission_uid = models.CharField(max_length=32)
    mission_file = models.CharField(max_length=255, blank=True)  # MFile: the map / mission file the server loaded
    started_at = models.DateTimeField(null=True)
    game_date = models.CharField(max_length=32, blank=True)
    game_time = models.CharField(max_length=32, blank=True)  # the in-game clock at the mission start
    elapsed_s = models.FloatField(default=0.0)  # mission time passed at the snapshot
    updated_at = models.DateTimeField()
    interval_s = models.FloatField(default=30.0)  # `[live] interval_s` when written: lets readers judge staleness
    is_running = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.mission_uid


class LivePlayer(models.Model):
    """One player online at the last snapshot of a `LiveMission`. Replaced wholesale with every snapshot."""

    mission_id: int
    player_id: int | None
    mission = models.ForeignKey(LiveMission, on_delete=models.CASCADE, related_name="players")
    # Set when the account is already known (so hiding the player takes effect at once); a first-time visitor has none.
    player = models.ForeignKey(Player, on_delete=models.SET_NULL, null=True, related_name="+")
    account_uuid = models.CharField(max_length=36)
    name = models.CharField(max_length=128, blank=True)  # "" for a player who connected and never spawned
    coalition = models.IntegerField(default=0)  # 0 = not known yet
    country = models.IntegerField(default=0)
    aircraft_type = models.CharField(max_length=128, blank=True)  # log name; "" without an open sortie
    aircraft_name = models.CharField(max_length=128, blank=True)  # display name at write time
    propulsion = models.CharField(max_length=4, choices=Propulsion.choices, blank=True, default="")
    state = models.CharField(max_length=10, choices=LiveState.choices)
    sortie_started_at = models.DateTimeField(null=True)
    flight_time_s = models.FloatField(default=0.0)
    kills_air = models.PositiveIntegerField(default=0)
    kills_ground = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["mission", "account_uuid"], name="liveplayer_unique")]

    def __str__(self) -> str:
        return self.name or self.account_uuid


class AircraftAmmoMixStats(models.Model):
    """Level 2 (FR-WEB-18): ammo mixes per victim aircraft type, the sum of `MissionAircraftAmmoMix` over the missions
    of the scope (rows as there: one per member ammo plus the `TOTAL_AMMO` row); `tour`, `role` and `mod_pattern` as on
    `AircraftAmmoStats`. Average hits of a member = `hits / kills`, divided when the page reads it (TD-22)."""

    aircraft_id: int
    tour_id: int | None

    aircraft = models.ForeignKey(GameObject, on_delete=models.PROTECT, related_name="ammo_mix_stats")
    tour = models.ForeignKey(Tour, null=True, on_delete=models.CASCADE, related_name="ammo_mix_stats")
    role = models.CharField(max_length=16, choices=AircraftRole.choices, default=AircraftRole.ALL)
    mod_pattern = models.CharField(max_length=16, blank=True, default="")
    mix = models.CharField(max_length=MIX_KEY_LENGTH)
    ammo = models.CharField(max_length=128)
    kills = models.PositiveIntegerField(default=0)
    hits = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = scoped_unique("aircraftammomixstats", ["aircraft", "role", "mod_pattern", "mix", "ammo"])

    def __str__(self) -> str:
        return f"{self.aircraft_id} / {self.mix} / {self.ammo}"
