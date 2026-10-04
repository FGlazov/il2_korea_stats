"""Events are immutable for pyright only (events.py `event_class`): the runtime classes are not frozen, for speed.

The `type: ignore` below is the proof: `reportUnnecessaryTypeIgnoreComment = "error"` makes pyright fail this file the
moment assigning to an event field stops being a type error (for example when `dataclass_transform(frozen_default=True)`
is dropped from the decorator).
"""

from il2ks.core.logparse.events import NO_OBJECT, HitEvent


def test_event_fields_are_read_only_for_pyright() -> None:
    hit = HitEvent(tick=1, ammo="explosion", attacker_id=NO_OBJECT, target_id=NO_OBJECT)
    hit.tick = 2  # type: ignore[misc]  # frozen_default=True via dataclass_transform
    assert hit.tick == 2  # runtime is not frozen: this is the documented trade-off
