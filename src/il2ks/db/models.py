"""Models: pre-aggregated read models (TD-08, doc 06). Level 1 = per mission, level 2 = across missions.

Natural keys (FR-ING-9, FR-WEB-13): mission `(server_uid, mission_uid)`, sortie `(mission, account_uuid, spawn_tick)`,
player `account_uuid`. Rows are upserted by them so PKs (and URLs) survive `reprocess`.
"""

from __future__ import annotations

from typing import ClassVar, Self

from django.db import models


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


class Counters(models.Model):
    """The counters shared by PlayerMission, Player and PlayerAircraft (doc 06, FR-WEB-4). No ratios (TD-22)."""

    sorties = models.PositiveIntegerField(default=0)
    flight_time_s = models.FloatField(default=0.0)
    kills_air = models.PositiveIntegerField(default=0)
    kills_ground = models.PositiveIntegerField(default=0)
    assists = models.PositiveIntegerField(default=0)
    deaths = models.PositiveIntegerField(default=0)
    planes_lost = models.PositiveIntegerField(default=0)
    bailouts = models.PositiveIntegerField(default=0)
    suspected_early_bailouts = models.PositiveIntegerField(default=0)
    captures = models.PositiveIntegerField(default=0)
    takeoffs = models.PositiveIntegerField(default=0)
    landings = models.PositiveIntegerField(default=0)
    # Friendly fire is tracked apart from the counters above (never in kills_*, assists)
    friendly_fire_incidents = models.PositiveIntegerField(default=0)  # sorties with at least one friendly kill
    friendly_kills = models.PositiveIntegerField(default=0)
    friendly_hits = models.PositiveIntegerField(default=0)
    friendly_damage = models.FloatField(default=0.0)
    # Ground losses, combat role and time on target (FR-WEB-19/20)
    taxi_accidents = models.PositiveIntegerField(default=0)
    strafed_on_ground = models.PositiveIntegerField(default=0)
    attack_sorties = models.PositiveIntegerField(default=0)
    time_on_target_s = models.FloatField(default=0.0)
    # Ground kills by category (they sum to kills_ground) and how many of those were static objects (OQ-33, doc 13)
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
    # Air-to-air Elo (OQ-28, FR-WEB-19). Order-dependent, so not a Counters sum: `ingest.ratings.recompute_ratings`
    # replays all qualifying kills. Defaults are the config's `[ratings] start` and 0 games.
    elo_prop = models.FloatField(default=1500.0)
    elo_jet = models.FloatField(default=1500.0)
    elo_prop_games = models.PositiveIntegerField(default=0)
    elo_jet_games = models.PositiveIntegerField(default=0)

    objects: ClassVar[HideableManager[Player]] = HideableManager()  # pyright: ignore[reportIncompatibleVariableOverride]

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
    is_hidden = models.BooleanField(default=False)
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
        indexes = [models.Index(fields=["-started_at"], name="mission_started_desc")]

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
    damage_taken = models.FloatField(default=0.0)
    disconnected = models.BooleanField(default=False)
    is_death = models.BooleanField(default=False)
    is_plane_lost = models.BooleanField(default=False)
    is_captured = models.BooleanField(default=False)
    loss_cause = models.CharField(max_length=10, choices=LossCause.choices)
    suspected_structural_failure = models.BooleanField(default=False)
    kills_air = models.PositiveIntegerField(default=0)
    kills_ground = models.PositiveIntegerField(default=0)
    assists = models.PositiveIntegerField(default=0)
    takeoffs = models.PositiveIntegerField(default=0)
    landings = models.PositiveIntegerField(default=0)
    friendly_kills = models.PositiveIntegerField(default=0)
    friendly_hits = models.PositiveIntegerField(default=0)
    friendly_damage = models.FloatField(default=0.0)
    resupplied = models.BooleanField(default=False)  # FR-ING-24: a landing followed by another takeoff
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
    ammo = models.CharField(max_length=128)
    kills = models.PositiveIntegerField(default=0)
    hits = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["mission", "aircraft", "ammo"], name="missionaircraftammo_unique")
        ]

    def __str__(self) -> str:
        return f"{self.mission_id} / {self.aircraft_id} / {self.ammo}"


# --- Level 2: across missions ---


class PlayerAircraft(Counters):
    """All-time counters per player and aircraft type (profile table, FR-WEB-4)."""

    player_id: int
    aircraft_id: int

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="aircraft_stats")
    aircraft = models.ForeignKey(GameObject, on_delete=models.PROTECT, related_name="player_stats")

    class Meta(Counters.Meta):
        abstract = False
        constraints = [models.UniqueConstraint(fields=["player", "aircraft"], name="playeraircraft_unique")]

    def __str__(self) -> str:
        return f"{self.player_id} / {self.aircraft_id}"


class PlayerTour(Counters):
    """A player's counters within one tour (TD-26): the sum of their `PlayerMission` rows of that tour's missions.

    Rows exist only for players with a counted sortie in the tour. Elo is not per tour (all-time on `Player`)."""

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

    class Meta(Counters.Meta):
        abstract = False
        constraints = [models.UniqueConstraint(fields=["player", "tour", "aircraft"], name="playertouraircraft_unique")]

    def __str__(self) -> str:
        return f"{self.player_id} / tour {self.tour_id} / {self.aircraft_id}"


class AircraftAmmoStats(models.Model):
    """Level 2 (FR-WEB-18): all-time hits to destroy per victim aircraft type and gun ammo.

    The sum of `MissionAircraftAmmo` over all missions. The average hits to destroy is `hits / kills`, computed when the
    page reads it (TD-22: no aggregation at request time). The `TOTAL_AMMO` row is all gun ammo together."""

    aircraft_id: int

    aircraft = models.ForeignKey(GameObject, on_delete=models.PROTECT, related_name="ammo_stats")
    ammo = models.CharField(max_length=128)
    kills = models.PositiveIntegerField(default=0)
    hits = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["aircraft", "ammo"], name="aircraftammostats_unique")]

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


class AircraftStats(Counters):
    """Level 2 (FR-WEB-8): all-time counters per aircraft type, summed over every pilot's `PlayerAircraft` row.

    Hidden players are included: hiding is presentation only (FR-ADM-3). `pilots` = distinct players who flew the type.
    `side` is the side most of its sorties were flown for ('redfor', 'blufor' or ''). The four ratio fields are
    fractions stored only so the list page can sort by them (TD-22); 0.0 where the denominator is 0, and the page shows
    a dash then: `kd` = air kills per death, `kl` = air kills per plane lost, `survival` = sorties without a death per
    sortie, `attack_share` = attack sorties per sortie (the rest are air-superiority sorties)."""

    aircraft_id: int

    aircraft = models.OneToOneField(GameObject, on_delete=models.PROTECT, related_name="stats")
    pilots = models.PositiveIntegerField(default=0)
    side = models.CharField(max_length=8, blank=True, default="")
    kd = models.FloatField(default=0.0)
    kl = models.FloatField(default=0.0)
    survival = models.FloatField(default=0.0)
    attack_share = models.FloatField(default=0.0)

    class Meta(Counters.Meta):
        abstract = False

    def __str__(self) -> str:
        return f"aircraft {self.aircraft_id}"


class AircraftMatchup(models.Model):
    """Level 2 (FR-WEB-8): player-versus-player air kills of one aircraft type against another.

    `kills` = `Kill` rows (credit `kill`, enemy, pilot sorties on both sides) where a `killer_aircraft` shot down a
    `victim_aircraft`. A type's losses to another type are the reversed pair. Pairs without kills have no row."""

    killer_aircraft_id: int
    victim_aircraft_id: int

    killer_aircraft = models.ForeignKey(GameObject, on_delete=models.PROTECT, related_name="matchup_kills")
    victim_aircraft = models.ForeignKey(GameObject, on_delete=models.PROTECT, related_name="matchup_losses")
    kills = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["killer_aircraft", "victim_aircraft"], name="aircraftmatchup_unique")
        ]

    def __str__(self) -> str:
        return f"{self.killer_aircraft_id} -> {self.victim_aircraft_id}"


class AircraftPayload(models.Model):
    """Level 2 (FR-WEB-8): counted sorties per aircraft type and loadout (`PlayerSortie.payload_name`; '' = unnamed)."""

    aircraft_id: int

    aircraft = models.ForeignKey(GameObject, on_delete=models.PROTECT, related_name="payload_stats")
    payload_name = models.CharField(max_length=128, blank=True)
    sorties = models.PositiveIntegerField(default=0)
    kills_air = models.PositiveIntegerField(default=0)
    kills_ground = models.PositiveIntegerField(default=0)
    deaths = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["aircraft", "payload_name"], name="aircraftpayload_unique")]

    def __str__(self) -> str:
        return f"{self.aircraft_id} / {self.payload_name}"


class PlayerKillboard(models.Model):
    """The killboard (FR-WEB-9): how often `player` and `opponent` shot each other down in PvP air combat.

    Every pair has two mirror rows, one per perspective, so a player's board is one indexed read: `kills` = times
    `player` got the kill credit on `opponent`, `deaths` = times `opponent` got it on `player`. Only kill credits count
    (not assists, not friendly fire) between two pilot sorties of different accounts. `last_at` / `last_mission` = the
    latest such kill in either direction. Level 2: recomputed per affected player by `ingest.pairs`."""

    player_id: int
    opponent_id: int
    last_mission_id: int

    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="killboard_rows")
    opponent = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="+")
    kills = models.PositiveIntegerField(default=0)
    deaths = models.PositiveIntegerField(default=0)
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


class PlayerStreak(models.Model):
    """Ironman streaks (FR-WEB-23): current and best run of survived sorties, rule in `il2ks.core.streaks`.

    A row exists for players with at least one survived sortie. All-time only. Level 2: recomputed per affected player
    by `ingest.streaks`."""

    player_id: int

    player = models.OneToOneField(Player, on_delete=models.CASCADE, related_name="streak")
    current_sorties = models.PositiveIntegerField(default=0)
    current_kills_air = models.PositiveIntegerField(default=0)
    current_flight_time_s = models.FloatField(default=0.0)
    current_since = models.DateTimeField(null=True)
    current_until = models.DateTimeField(null=True)
    best_sorties = models.PositiveIntegerField(default=0)
    best_kills_air = models.PositiveIntegerField(default=0)
    best_flight_time_s = models.FloatField(default=0.0)
    best_since = models.DateTimeField(null=True)
    best_until = models.DateTimeField(null=True)

    class Meta:
        indexes = [models.Index(fields=["-current_sorties"], name="streak_current_desc")]

    def __str__(self) -> str:
        return f"{self.player_id}: {self.current_sorties} / {self.best_sorties}"


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


class SiteSettings(models.Model):
    """Branding and site texts, edited in the admin (FR-ADM-2, TD-25). A singleton: always pk=1 (`il2ks.db.site`)."""

    site_title = models.CharField(max_length=100, default="IL-2 Korea stats")
    server_name = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)  # shown on the home page
    # Path relative to MEDIA_ROOT of the re-encoded logo (raster only, never SVG; FR-ADM-2). Empty = no logo.
    logo = models.CharField(max_length=200, blank=True)
    # "#RRGGBB" mapped onto Pico's primary color; empty = the default theme
    accent_color = models.CharField(max_length=7, blank=True)
    links: models.JSONField[list[dict[str, str]]] = models.JSONField(default=list, blank=True)  # [{"label", "url"}]
    # Coalition display names (FR-ADM-5, doc 06): 5xx countries are REDFOR, 6xx BLUFOR.
    redfor_name = models.CharField(max_length=40, default="REDFOR")
    blufor_name = models.CharField(max_length=40, default="BLUFOR")
    redfor_emblem = models.CharField(max_length=10, choices=RedforEmblem.choices, default=RedforEmblem.NEUTRAL)
    blufor_emblem = models.CharField(max_length=10, choices=BluforEmblem.choices, default=BluforEmblem.NEUTRAL)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "site settings"
        verbose_name_plural = "site settings"

    def __str__(self) -> str:
        return self.site_title


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
