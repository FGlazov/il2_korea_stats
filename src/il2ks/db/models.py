"""Models. Iteration 0 ships only the object catalog, so the portability and constraint harnesses have a real table."""

from django.db import models


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
    UNKNOWN = "unknown"


class GameObject(models.Model):
    """A game object type from the logs (aircraft, vehicle, ...). Unknown types are auto-registered (FR-ING-7)."""

    log_name = models.CharField(max_length=128, unique=True)
    display_name = models.CharField(max_length=128)
    cls = models.CharField(max_length=16, choices=ObjectClass.choices, default=ObjectClass.UNKNOWN)
    is_playable = models.BooleanField(default=False)
    is_known = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(cls__in=ObjectClass.values),
                name="gameobject_cls_valid",
            ),
            models.CheckConstraint(condition=~models.Q(log_name=""), name="gameobject_log_name_not_empty"),
        ]

    def __str__(self) -> str:
        return self.display_name or self.log_name
