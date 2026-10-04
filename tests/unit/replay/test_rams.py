"""Ram detection signal (doc 12/13): two aircraft destroyed together with no gun hits between them."""

from il2ks.core.catalog.loader import ObjectInfo
from il2ks.core.logparse.events import ObjectId, Pos
from il2ks.core.replay.model import HitRecord, TrackedObject
from il2ks.core.replay.toggles import RuleToggles, detect_rams

INFO = ObjectInfo("F-86A-5", "F-86A-5", "fighter", True, True)


def _plane(object_id: int, tick: int, x: float) -> TrackedObject:
    obj = TrackedObject(ObjectId(object_id), "F-86A-5", 501, 1, INFO, is_bot=False)
    obj.destroyed_tick = tick
    obj.destroyed_pos = Pos(x, 1000.0, 0.0)
    obj.destroyed_airborne = True
    return obj


def test_close_simultaneous_destruction_is_a_ram() -> None:
    a, b = _plane(1, 1000, 0.0), _plane(2, 1010, 10.0)
    rams = detect_rams([a, b], RuleToggles())
    assert len(rams) == 1
    assert rams[0].tick == 1000


def test_gun_hits_or_distance_or_time_rule_it_out() -> None:
    a, b = _plane(1, 1000, 0.0), _plane(2, 1010, 10.0)
    a.hit_log.append(HitRecord(900, b, "BULLET_20mm"))
    assert detect_rams([a, b], RuleToggles()) == []
    assert detect_rams([_plane(3, 1000, 0.0), _plane(4, 1010, 500.0)], RuleToggles()) == []
    assert detect_rams([_plane(5, 1000, 0.0), _plane(6, 1500, 10.0)], RuleToggles()) == []
