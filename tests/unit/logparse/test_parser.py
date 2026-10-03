"""Parser unit tests: one real-format line per AType -> the expected event (FR-ING-3, TD-20, doc 12).

IDs, names and UUIDs are made up; the line shapes are copied from the Korea logs.
"""

from types import MappingProxyType

import pytest

from il2ks.core.logparse.events import (
    AccountUuid,
    AirfieldEvent,
    AreaBoundaryEvent,
    BailoutEvent,
    BotRemovedEvent,
    DamageEvent,
    GenericEvent,
    GroupEvent,
    GunBurstEvent,
    HitEvent,
    InfluenceAreaEvent,
    KillEvent,
    LandingEvent,
    LogEvent,
    LogVersionEvent,
    MissionEndEvent,
    MissionObjectiveEvent,
    MissionStartEvent,
    ObjectId,
    ObjectSpawnEvent,
    PlayerConnectEvent,
    PlayerDisconnectEvent,
    PlayerSpawnEvent,
    Pos,
    ProfileUuid,
    RocketFiredEvent,
    RoundEndEvent,
    SortieEndEvent,
    StoreReleaseEvent,
    TakeoffEvent,
    WheelsOffEvent,
    WheelsOnEvent,
)
from il2ks.core.logparse.parser import (
    MAX_WARNINGS,
    MAX_WARNINGS_PER_KIND,
    RAW_FIELD,
    UNLABELLED,
    ParseError,
    ParseStats,
    WarningKind,
    parse_line,
    parse_lines,
    tokenize,
)

ACCOUNT = "00000000-0000-4000-8000-00000000a001"
PROFILE = "00000000-0000-4000-8000-00000000b001"


def oid(n: int) -> ObjectId:
    return ObjectId(n)


def extra(**kv: str) -> MappingProxyType[str, str]:
    return MappingProxyType(kv)


SPAWN_LINE = (
    "T:8041 AType:10 PLID:8193 PID:9217 BUL:1800 SH:0 BOMB:6 RCT:0 (143635.7031,80.3996,350480.5312) "
    f"IDS:{PROFILE} LOGIN:{ACCOUNT} NAME:Test Pilot TYPE:F-80C-10 COUNTRY:601 FORM:0 FIELD:422912 INAIR:2 "
    "PARENT:-1 ISPL:1 ISTSTART:1 PAYLOAD:13 FUEL:1.0000 SKIN: WM:5 "
)
SPAWN_EVENT = PlayerSpawnEvent(
    tick=8041,
    aircraft_id=oid(8193),
    bot_id=oid(9217),
    bullets=1800,
    shells=0,
    bombs=6,
    rockets=0,
    pos=Pos(143635.7031, 80.3996, 350480.5312),
    profile_uuid=ProfileUuid(PROFILE),
    account_uuid=AccountUuid(ACCOUNT),
    name="Test Pilot",
    aircraft_type="F-80C-10",
    country=601,
    form=0,
    airfield_id=oid(422912),
    in_air=2,
    parent_id=oid(-1),
    is_player=True,
    is_tstart=True,
    payload_id=13,
    fuel=1.0,
    skin="",
    weapon_mods=5,
)

CASES: list[tuple[str, LogEvent]] = [
    (
        "T:0 AType:0 GDate:1951.4.24 GTime:12:0:0 MFile:Multiplayer/Dogfight\\Test\\Test_Mission.msnbin MID: GType:2 "
        "CNTRS:0:0,501:1,502:1,503:1,601:2 SETTS:00010001111010000000000010000110 MODS:0 PRESET:0 AQMID:0 "
        "ROUNDS: 1 POINTS: 10000",
        MissionStartEvent(
            tick=0,
            extra=extra(ROUNDS="1", POINTS="10000"),
            game_date="1951.4.24",
            game_time="12:0:0",
            mission_file="Multiplayer/Dogfight\\Test\\Test_Mission.msnbin",
            mission_id="",
            game_type=2,
            countries=MappingProxyType({0: 0, 501: 1, 502: 1, 503: 1, 601: 2}),
            settings="00010001111010000000000010000110",
            mods=0,
            preset=0,
            aqm_id=0,
        ),
    ),
    (
        "T:82163 AType:1 AMMO:explosion AID:58371 TID:377856",
        HitEvent(tick=82163, ammo="explosion", attacker_id=oid(58371), target_id=oid(377856)),
    ),
    (
        "T:82164 AType:1 AMMO:BULLET_12-7_USA_API AID:58371 TID:8192",
        HitEvent(tick=82164, ammo="BULLET_12-7_USA_API", attacker_id=oid(58371), target_id=oid(8192)),
    ),
    (
        "T:33941 AType:2 DMG:1.0000 AID:-1 TID:10243 POS(142540.2500,80.9976,349736.0000)",
        DamageEvent(
            tick=33941, damage=1.0, attacker_id=oid(-1), target_id=oid(10243), pos=Pos(142540.25, 80.9976, 349736.0)
        ),
    ),
    (
        "T:15014 AType:3 AID:-1 TID:935936 POS(249176.6562,12.2672,381041.5938)",
        KillEvent(tick=15014, attacker_id=oid(-1), target_id=oid(935936), pos=Pos(249176.6562, 12.2672, 381041.5938)),
    ),
    (
        "T:34855 AType:4 PLID:10243 PID:11267 BUL:1800 SH:0 BOMB:6 RCT:0 (142300.8125,76.9055,349617.3125)",
        SortieEndEvent(
            tick=34855,
            aircraft_id=oid(10243),
            bot_id=oid(11267),
            bullets=1800,
            shells=0,
            bombs=6,
            rockets=0,
            pos=Pos(142300.8125, 76.9055, 349617.3125),
        ),
    ),
    (
        "T:93381 AType:4 PLID:0 PID:9217 BUL:0 SH:0 BOMB:0 RCT:0 (0.0000,0.0000,0.0000)",
        SortieEndEvent(
            tick=93381, aircraft_id=oid(0), bot_id=oid(9217), bullets=0, shells=0, bombs=0, rockets=0, pos=Pos(0, 0, 0)
        ),
    ),
    (
        "T:34530 AType:5 PID:8193 POS(142367.0469, 99.4992, 349678.2500)",
        TakeoffEvent(tick=34530, object_id=oid(8193), pos=Pos(142367.0469, 99.4992, 349678.25)),
    ),
    (
        "T:93400 AType:6 PID:8193 POS(195912.0781, 0.7228, 419177.8750)",
        LandingEvent(tick=93400, object_id=oid(8193), pos=Pos(195912.0781, 0.7228, 419177.875)),
    ),
    ("T:538793 AType:7 ", MissionEndEvent(tick=538793)),
    (
        "T:535843 AType:8 OBJID:27971 POS(523053.2500,0.0000,264362.9062) COAL:2 TYPE:0 RES:1 ICTYPE:0 "
        "TARGETS() OBJECTS() PLANES() MTARGETS() MOBJETS()",
        MissionObjectiveEvent(
            tick=535843,
            extra=extra(TARGETS="()", OBJECTS="()", PLANES="()", MTARGETS="()", MOBJETS="()"),
            object_id=oid(27971),
            pos=Pos(523053.25, 0.0, 264362.9062),
            coalition=2,
            objective_type=0,
            result=1,
            icon_type=0,
        ),
    ),
    (
        "T:10 AType:9 AID:422912 COUNTRY:601 POS(143635.7188, 79.2295, 350480.5000) IDS()",
        AirfieldEvent(
            tick=10,
            extra=extra(IDS="()"),
            airfield_id=oid(422912),
            country=601,
            pos=Pos(143635.7188, 79.2295, 350480.5),
        ),
    ),
    (SPAWN_LINE, SPAWN_EVENT),
    (
        "T:1 AType:11 GID:1188864 IDS:443392,447488,453632 LID:443392",
        GroupEvent(
            tick=1, group_id=oid(1188864), member_ids=(oid(443392), oid(447488), oid(453632)), leader_id=oid(443392)
        ),
    ),
    (
        "T:1 AType:11 GID:1188864 IDS: LID:-1",
        GroupEvent(tick=1, group_id=oid(1188864), member_ids=(), leader_id=oid(-1)),
    ),
    (
        "T:8041 AType:12 ID:8193 TYPE:F-80C-10 COUNTRY:601 NAME:noname PID:-1 "
        "POS(143635.7031,80.3416,350480.5625) MID:-1",
        ObjectSpawnEvent(
            tick=8041,
            extra=extra(MID="-1"),
            object_id=oid(8193),
            object_type="F-80C-10",
            country=601,
            name="noname",
            parent_id=oid(-1),
            pos=Pos(143635.7031, 80.3416, 350480.5625),
        ),
    ),
    (
        "T:15014 AType:12 ID:935936 TYPE:M-1919 AA COUNTRY:501 NAME:M-1919 AA PID:934912 "
        "POS(249176.6562,12.2672,381041.5938) MID:-1",
        ObjectSpawnEvent(
            tick=15014,
            extra=extra(MID="-1"),
            object_id=oid(935936),
            object_type="M-1919 AA",
            country=501,
            name="M-1919 AA",
            parent_id=oid(934912),
            pos=Pos(249176.6562, 12.2672, 381041.5938),
        ),
    ),
    (
        "T:20 AType:12 ID:40960 TYPE:Landing Ship, Tank COUNTRY:601 NAME:LST, block 2 PID:-1 "
        "POS(1.0,2.0,3.0) MID:74052",
        ObjectSpawnEvent(
            tick=20,
            extra=extra(MID="74052"),
            object_id=oid(40960),
            object_type="Landing Ship, Tank",
            country=601,
            name="LST, block 2",
            parent_id=oid(-1),
            pos=Pos(1.0, 2.0, 3.0),
        ),
    ),
    (
        "T:20 AType:12 ID:40961 TYPE:Dugout A[15585,0] COUNTRY:501 NAME:Block PID:-1 POS(1.0,2.0,3.0) MID:15585",
        ObjectSpawnEvent(
            tick=20,
            extra=extra(MID="15585"),
            object_id=oid(40961),
            object_type="Dugout A[15585,0]",
            country=501,
            name="Block",
            parent_id=oid(-1),
            pos=Pos(1.0, 2.0, 3.0),
        ),
    ),
    (
        "T:0 AType:13 AID:1138688 COUNTRY:601 ENABLED:1 BC(0,0,1)",
        InfluenceAreaEvent(tick=0, area_id=oid(1138688), country=601, enabled=True, bc=(0, 0, 1)),
    ),
    (
        "T:1 AType:14 AID:1138688 BP((124176.0,215025.0),(140017.0,240505.0),(146303.0,271437.0))",
        AreaBoundaryEvent(
            tick=1,
            area_id=oid(1138688),
            points=((124176.0, 215025.0), (140017.0, 240505.0), (146303.0, 271437.0)),
        ),
    ),
    ("T:1 AType:14 AID:7 BP()", AreaBoundaryEvent(tick=1, area_id=oid(7), points=())),
    ("T:150 AType:15 VER:18", LogVersionEvent(tick=150, version=18)),
    (
        "T:22775 AType:16 BOTID:10242 POS(166800.9844,199.3510,373230.6250)",
        BotRemovedEvent(tick=22775, bot_id=oid(10242), pos=Pos(166800.9844, 199.351, 373230.625)),
    ),
    (
        "T:218313 AType:18 BOTID:2388992 PARENTID:2387968 POS(156931.9062,497.1961,177527.2031)",
        BailoutEvent(
            tick=218313, bot_id=oid(2388992), parent_id=oid(2387968), pos=Pos(156931.9062, 497.1961, 177527.2031)
        ),
    ),
    ("T:542763 AType:19 ", RoundEndEvent(tick=542763)),
    (
        f"T:4392 AType:20 USERID:{ACCOUNT} USERNICKID:{PROFILE}",
        PlayerConnectEvent(tick=4392, account_uuid=AccountUuid(ACCOUNT), profile_uuid=ProfileUuid(PROFILE)),
    ),
    (
        f"T:22829 AType:21 USERID:{ACCOUNT} USERNICKID:{PROFILE}",
        PlayerDisconnectEvent(tick=22829, account_uuid=AccountUuid(ACCOUNT), profile_uuid=ProfileUuid(PROFILE)),
    ),
    (
        "T:76829 AType:24 OBJID:941056 POS(165367.0938,163.7552,301842.1875)",
        GunBurstEvent(tick=76829, object_id=oid(941056), pos=Pos(165367.0938, 163.7552, 301842.1875)),
    ),
    (
        "T:34329 AType:25 OBJID:8193 POS(142633.9844,91.0489,349810.3125) TID:34817",
        StoreReleaseEvent(
            tick=34329, object_id=oid(8193), pos=Pos(142633.9844, 91.0489, 349810.3125), store_id=oid(34817)
        ),
    ),
    (
        "T:230248 AType:26 OBJID:105484 POS(143635.8594,80.7084,350480.1875) TID:38924",
        RocketFiredEvent(
            tick=230248, object_id=oid(105484), pos=Pos(143635.8594, 80.7084, 350480.1875), rocket_id=oid(38924)
        ),
    ),
    (
        "T:93379 AType:27 OBJID:8193 POS(195910.0312,0.2500,419178.5312)",
        GenericEvent(
            tick=93379,
            atype=27,
            fields=MappingProxyType({"OBJID": "8193", "POS": "(195910.0312,0.2500,419178.5312)"}),
        ),
    ),
    (
        "T:8031 AType:28 OBJID:8193 POS(143635.7344,80.4842,350480.4688)",
        GenericEvent(
            tick=8031,
            atype=28,
            fields=MappingProxyType({"OBJID": "8193", "POS": "(143635.7344,80.4842,350480.4688)"}),
        ),
    ),
    (
        "T:34280 AType:30 ID:8193 POS(142689.8750,88.7367,349838.2500)",
        WheelsOffEvent(tick=34280, object_id=oid(8193), pos=Pos(142689.875, 88.7367, 349838.25)),
    ),
    (
        "T:8050 AType:31 ID:8193 POS(143635.6875,80.3230,350480.5625)",
        WheelsOnEvent(tick=8050, object_id=oid(8193), pos=Pos(143635.6875, 80.323, 350480.5625)),
    ),
]


@pytest.mark.parametrize(("line", "expected"), CASES, ids=[c[0][:30] for c in CASES])
def test_parse_line(line: str, expected: LogEvent) -> None:
    assert parse_line(line) == expected


@pytest.mark.parametrize(("line", "expected"), CASES, ids=[c[0][:30] for c in CASES])
def test_parse_line_tolerates_crlf_and_trailing_spaces(line: str, expected: LogEvent) -> None:
    assert parse_line(line + "  \r\n") == expected


def test_player_names_with_spaces_commas_and_colons_anchor_on_next_key() -> None:
    line = SPAWN_LINE.replace("NAME:Test Pilot", "NAME:[JG]  Ace, the: Pilot ").replace(
        "SKIN: WM", "SKIN:f86a5/my skin, v2#1.dds WM"
    )
    event = parse_line(line)
    assert isinstance(event, PlayerSpawnEvent)
    assert event.name == "[JG]  Ace, the: Pilot "
    assert event.skin == "f86a5/my skin, v2#1.dds"
    assert event.aircraft_type == "F-80C-10"


def test_aircraft_type_with_spaces() -> None:
    event = parse_line(SPAWN_LINE.replace("TYPE:F-80C-10", "TYPE:Some New Plane"))
    assert isinstance(event, PlayerSpawnEvent)
    assert event.aircraft_type == "Some New Plane"


def test_mission_file_with_spaces() -> None:
    event = parse_line(CASES[0][0].replace("Test_Mission", "Test Mission"))
    assert isinstance(event, MissionStartEvent)
    assert event.mission_file == "Multiplayer/Dogfight\\Test\\Test Mission.msnbin"


def test_empty_name_value() -> None:
    event = parse_line("T:1 AType:12 ID:5 TYPE:Thing COUNTRY:0 NAME: PID:-1 POS(1,2,3) MID:-1")
    assert isinstance(event, ObjectSpawnEvent)
    assert event.name == ""


def test_unknown_trailing_keys_go_to_extra() -> None:
    event = parse_line("T:5 AType:5 PID:8193 POS(1.0,2.0,3.0) NEWKEY:abc NEWGROUP(1,2)")
    assert event == TakeoffEvent(
        tick=5, object_id=oid(8193), pos=Pos(1, 2, 3), extra=extra(NEWKEY="abc", NEWGROUP="(1,2)")
    )


def test_unknown_key_in_the_middle_of_a_free_text_anchored_line() -> None:
    event = parse_line("T:1 AType:12 ID:5 TYPE:Big Thing NEW:1 COUNTRY:0 NAME:x PID:-1 POS(1,2,3) MID:-1")
    assert isinstance(event, ObjectSpawnEvent)
    # TYPE runs to the next *known* key, so an unknown key in between is swallowed into it rather than lost.
    assert event.object_type == "Big Thing NEW:1"


@pytest.mark.parametrize(
    "line",
    [
        "",
        "   ",
        "garbage",
        "T:abc AType:1 AMMO:x AID:1 TID:2",
        "T:1 AType:x",
        "T:1AType:1 AMMO:x AID:1 TID:2",
        "T:1 AType:1",  # missing every key
        "T:1 AType:1 AMMO:x AID:1",  # truncated: TID missing
        "T:1 AType:1 AMMO:x AID:1 TI",  # truncated mid-key
        "T:1 AType:2 DMG:0.1 AID:1 TID:2 POS(1.0,2.0",  # truncated mid-position
        "T:1 AType:2 DMG:0.1 AID:1 TID:2 POS(1.0,2.0)",  # 2 coordinates
        "T:1 AType:2 DMG:0.1 AID:1 TID:2 POS(1.0,x,2.0)",
        "T:1 AType:2 DMG:abc AID:1 TID:2 POS(1,2,3)",
        "T:1 AType:2 DMG:0.1 AID:1 TID:2 POS:1",  # POS not parenthesized
        "T:1 AType:2 DMG:0.1 AID:1 TID:2 POS(1,2,3)x",  # junk glued to a group
        "T:1 AType:1 AMMO:x AID:1.5 TID:2",
        "T:1 AType:1 AMMO:x AID:1 AID:2 TID:3",  # duplicate key
        "T:1 AType:1 AMMO:x AID:1 TID:2 stray",
        "T:1 AType:11 GID:1 IDS:1,x LID:1",
        "T:1 AType:13 AID:1 COUNTRY:601 ENABLED:2 BC(0,0,0)",
        "T:1 AType:13 AID:1 COUNTRY:601 ENABLED:1 BC(0,x,0)",
        "T:1 AType:14 AID:1 BP((1.0,2.0,3.0))",  # 3D boundary point
        "T:1 AType:14 AID:1 BP((1.0,x))",
        "T:1 AType:14 AID:1 BP(1.0,2.0)",
        "T:1 AType:14 AID:1 BP((1.0,2.0)",  # unbalanced
        "T:1 AType:0 GDate:1 GTime:1 MFile:x MID: GType:2 CNTRS:0:0,501 SETTS:1 MODS:0 PRESET:0 AQMID:0",
        "T:1 AType:0 GDate:1 GTime:1 MFile:x MID: GType:2 CNTRS:a:0 SETTS:1 MODS:0 PRESET:0 AQMID:0",
        SPAWN_LINE.replace(" WM:5 ", ""),  # truncated after SKIN
        SPAWN_LINE[:60],
    ],
)
def test_malformed_lines_raise_parse_error(line: str) -> None:
    with pytest.raises(ParseError):
        parse_line(line)


def test_unknown_atype_is_a_generic_event() -> None:
    assert parse_line("T:7 AType:99 FOO:1 BAR(2,3) (4,5,6)") == GenericEvent(
        tick=7, atype=99, fields=MappingProxyType({"FOO": "1", "BAR": "(2,3)", UNLABELLED: "(4,5,6)"})
    )


def test_unknown_atype_with_untokenizable_text_is_still_a_generic_event() -> None:
    assert parse_line("T:7 AType:99 some free text") == GenericEvent(
        tick=7, atype=99, fields=MappingProxyType({RAW_FIELD: "some free text"})
    )


def test_tokenize_space_after_colon() -> None:
    assert tokenize("A: 1 B: C:3 D:") == {"A": "1", "B": "", "C": "3", "D": ""}


def test_tokenize_nested_groups() -> None:
    assert tokenize("X((1,2),(3,4)) Y:1") == {"X": "((1,2),(3,4))", "Y": "1"}


def test_parse_lines_counts_bad_lines_and_never_raises() -> None:
    stats = ParseStats()
    lines = [
        "T:150 AType:15 VER:18",
        "",
        "T:1 AType:1 AMMO:x AID:1",
        "garbage",
        "T:2 AType:1 AMMO:x AID:1 TID:2",
    ]
    events = list(parse_lines(lines, stats))
    assert [type(e) for e in events] == [LogVersionEvent, HitEvent]
    assert stats.lines_total == 4
    assert stats.lines_bad == 2
    assert stats.log_version == 18
    assert len(stats.warnings) == 2
    assert stats.warnings[0].startswith("line 3: missing key TID")


def test_parse_lines_caps_warning_texts_per_kind_and_summarizes() -> None:
    stats = ParseStats()
    assert list(parse_lines(["bad"] * (MAX_WARNINGS_PER_KIND + 10), stats)) == []
    assert stats.lines_bad == MAX_WARNINGS_PER_KIND + 10
    assert stats.warning_counts == {WarningKind.MALFORMED_LINE: MAX_WARNINGS_PER_KIND + 10}
    assert len(stats.warnings) == MAX_WARNINGS_PER_KIND + 1
    assert stats.warnings[-1] == "10 more 'malformed line' warnings suppressed"


def test_no_summary_when_nothing_was_suppressed() -> None:
    stats = ParseStats()
    list(parse_lines(["bad"] * MAX_WARNINGS_PER_KIND, stats))
    assert len(stats.warnings) == MAX_WARNINGS_PER_KIND
    assert not any("suppressed" in w for w in stats.warnings)


def test_a_flood_of_one_kind_does_not_hide_another_kind() -> None:
    stats = ParseStats()
    lines = ["bad"] * 500 + ["T:1 AType:1 AMMO:x AID:1"]  # a missing key after the flood
    list(parse_lines(lines, stats))
    assert stats.lines_bad == 501
    assert any("missing key TID" in w for w in stats.warnings)
    assert stats.warning_counts == {WarningKind.MALFORMED_LINE: 500, WarningKind.MISSING_KEY: 1}


def test_global_cap_applies_to_repeats_but_not_to_the_first_of_a_kind() -> None:
    stats = ParseStats()
    # Distinct unknown keys are all news, so they pass the caps and can fill the global cap ...
    lines = [f"T:1 AType:5 PID:1 POS(1,2,3) K{i}:1" for i in range(MAX_WARNINGS + 5)]
    list(parse_lines(lines, stats))
    assert len(stats.warnings) == MAX_WARNINGS + 5
    # ... then the first of another kind still gets through, and its repeats are only summarized.
    more = ["bad", "bad", "T:1 AType:1 AMMO:x AID:1 AID:2 TID:3", "T:1 AType:1 AMMO:x AID:1 AID:2 TID:3"]
    list(parse_lines(more, stats))
    texts = stats.warnings[MAX_WARNINGS + 5 :]
    assert len(texts) == 2 + 2  # the two firsts and two summaries
    assert "1 more 'malformed line' warnings suppressed" in texts
    assert "1 more 'duplicate key' warnings suppressed" in texts


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        ("garbage", WarningKind.MALFORMED_LINE),
        ("T:abc AType:1 AMMO:x AID:1 TID:2", WarningKind.MALFORMED_LINE),
        ("T:1 AType:1 AMMO:x AID:1 TID:2 stray", WarningKind.MALFORMED_TOKEN),
        ("T:1 AType:2 DMG:0.1 AID:1 TID:2 POS(1,2,3)x", WarningKind.MALFORMED_TOKEN),
        ("T:1 AType:2 DMG:0.1 AID:1 TID:2 POS(1.0,2.0", WarningKind.UNBALANCED_PARENTHESES),
        ("T:1 AType:14 AID:1 BP((1.0,2.0)", WarningKind.UNBALANCED_PARENTHESES),
        ("T:1 AType:1 AMMO:x AID:1 AID:2 TID:3", WarningKind.DUPLICATE_KEY),
        ("T:1 AType:1 AMMO:x AID:1", WarningKind.MISSING_KEY),
        ("T:1 AType:1", WarningKind.MISSING_KEY),
        ("T:1 AType:1 AMMO:x AID:1.5 TID:2", WarningKind.BAD_VALUE),
        ("T:1 AType:2 DMG:abc AID:1 TID:2 POS(1,2,3)", WarningKind.BAD_VALUE),
        ("T:1 AType:2 DMG:0.1 AID:1 TID:2 POS(1.0,2.0)", WarningKind.BAD_VALUE),
        ("T:1 AType:13 AID:1 COUNTRY:601 ENABLED:2 BC(0,0,0)", WarningKind.BAD_VALUE),
    ],
)
def test_parse_error_kind(line: str, kind: WarningKind) -> None:
    with pytest.raises(ParseError) as info:
        parse_line(line)
    assert info.value.kind is kind


def test_each_bad_line_kind_is_warned_and_counted() -> None:
    stats = ParseStats()
    lines = [
        "garbage",
        "T:1 AType:1 AMMO:x AID:1",
        "T:1 AType:1 AMMO:x AID:y TID:2",
        "T:1 AType:1 AMMO:x AID:1 AID:2 TID:3",
    ]
    list(parse_lines(lines, stats))
    assert stats.warning_counts == {
        WarningKind.MALFORMED_LINE: 1,
        WarningKind.MISSING_KEY: 1,
        WarningKind.BAD_VALUE: 1,
        WarningKind.DUPLICATE_KEY: 1,
    }
    assert len(stats.warnings) == 4


def test_first_occurrence_of_each_unknown_key_and_atype_is_warned_even_past_the_per_kind_cap() -> None:
    stats = ParseStats()
    lines = [f"T:1 AType:5 PID:1 POS(1,2,3) NEW:{i}" for i in range(MAX_WARNINGS_PER_KIND * 2)]  # one key, repeated
    lines += ["T:1 AType:5 PID:1 POS(1,2,3) OTHER:1", "T:1 AType:6 PID:1 POS(1,2,3) NEW:1"]  # new "<atype>:<KEY>"s
    lines += ["T:1 AType:40 X:1", "T:1 AType:40 X:2", "T:1 AType:41 X:1"]
    list(parse_lines(lines, stats))
    texts = stats.warnings
    assert sum("unknown key NEW in AType 5" in t for t in texts) == MAX_WARNINGS_PER_KIND
    assert any("unknown key OTHER in AType 5" in t for t in texts)
    assert any("unknown key NEW in AType 6" in t for t in texts)
    assert any("unknown AType 41" in t for t in texts)
    assert sum("unknown AType 40" in t for t in texts) == 2  # the kind is under its cap
    assert stats.warning_counts[WarningKind.UNKNOWN_KEY] == MAX_WARNINGS_PER_KIND * 2 + 2
    assert stats.warning_counts[WarningKind.UNKNOWN_ATYPE] == 3
    assert stats.warnings_emitted[WarningKind.UNKNOWN_KEY] == MAX_WARNINGS_PER_KIND + 2


def test_unknown_key_summary_counts_suppressed_ones() -> None:
    stats = ParseStats()
    n = MAX_WARNINGS_PER_KIND + 5
    list(parse_lines(["T:1 AType:5 PID:1 POS(1,2,3) NEW:1"] * n, stats))
    assert stats.warnings[-1] == "5 more 'unknown key' warnings suppressed"
    assert len(stats.warnings) == MAX_WARNINGS_PER_KIND + 1
    assert stats.unknown_keys == {"5:NEW": n}  # the Counter is never capped


def test_ignored_atypes_do_not_warn() -> None:
    stats = ParseStats()
    list(parse_lines(["T:1 AType:27 OBJID:1 POS(1,2,3)"], stats))
    assert stats.warnings == []


def test_parse_lines_counts_unknown_keys_and_atypes() -> None:
    stats = ParseStats()
    lines = [
        "T:1 AType:12 ID:5 TYPE:x COUNTRY:0 NAME:y PID:-1 POS(1,2,3) MID:-1",  # MID is expected, not unknown
        "T:1 AType:5 PID:1 POS(1,2,3) NEW:1",
        "T:1 AType:5 PID:1 POS(1,2,3) NEW:2",
        "T:1 AType:27 OBJID:1 POS(1,2,3)",
        "T:1 AType:28 OBJID:1 POS(1,2,3)",
        "T:1 AType:22 X:1",
        "T:1 AType:40 X:1",
    ]
    assert len(list(parse_lines(lines, stats))) == 7
    assert stats.unknown_keys == {"5:NEW": 2}
    assert stats.unknown_atypes == {22: 1, 40: 1}
    assert stats.ignored_atypes == {27: 1, 28: 1}
    assert stats.lines_bad == 0


def test_parse_lines_warns_on_log_version_change() -> None:
    stats = ParseStats()
    list(parse_lines(["T:1 AType:15 VER:18", "T:2 AType:15 VER:19", "T:3 AType:15 VER:18"], stats))
    assert stats.log_version == 18
    assert stats.warnings == ["line 2: log version changed from 18 to 19"]
    assert stats.warning_counts == {WarningKind.VERSION_CHANGE: 1}
