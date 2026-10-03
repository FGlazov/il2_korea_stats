"""The admin (FR-ADM-1..5): site settings, logo upload, hiding, read-only ingested rows, ingestion status."""

import io
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from PIL import Image
from pytest_django.fixtures import Settings

from il2ks.core.catalog.loader import load_default_catalog
from il2ks.db.models import (
    CompletionReason,
    Country,
    GameObject,
    IngestRun,
    IngestStatus,
    Mission,
    ObjectClass,
    Player,
    PlayerName,
    SiteSettings,
)
from il2ks.db.site import current_data_version, get_site_settings
from tests.factories import mission, save, sortie

pytestmark = pytest.mark.django_db

NOW = datetime.now(UTC).replace(microsecond=0) - timedelta(
    hours=1
)  # recent, so the status page's 30-day window has them


@pytest.fixture
def media_root(tmp_path: Path, settings: Settings) -> Path:
    settings.MEDIA_ROOT = tmp_path / "media"
    return tmp_path / "media"


@pytest.fixture
def admin(client: Client) -> Client:
    client.force_login(User.objects.create_superuser("boss", "boss@example.org", "x"))
    return client


def png(width: int = 120, height: int = 40, colour: str = "red") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(out, format="PNG")
    return out.getvalue()


def settings_url() -> str:
    return "/admin/il2ks_db/sitesettings/1/change/"


def settings_form(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "site_title": "Korea Fighters",
        "server_name": "Fighter Server",
        "description": "Welcome",
        "accent_color": "#1a73e8",
        "links_text": "Discord | https://discord.gg/example\nHomepage | http://example.org/",
        "redfor_name": "Red",
        "blufor_name": "Blue",
    }
    data.update(overrides)
    return data


# --- access ---


def test_admin_needs_a_staff_login(client: Client) -> None:
    for url in ("/admin/", "/admin/ingestion/", "/admin/il2ks_db/player/"):
        response = client.get(url)
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]


def test_staff_without_permissions_cannot_see_the_status_page(client: Client) -> None:
    client.force_login(User.objects.create_user("clerk", password="x", is_staff=True))

    assert client.get("/admin/ingestion/").status_code == 403


def test_admin_titles_follow_site_settings(admin: Client) -> None:
    SiteSettings.objects.update_or_create(pk=1, defaults={"site_title": "Fighter Wing Stats"})

    body = admin.get("/admin/").content.decode()

    assert "Fighter Wing Stats" in body
    assert "Django administration" not in body
    assert "/admin/ingestion/" in body  # linked from the index


def test_login_page_works_before_any_site_settings_exist(client: Client) -> None:
    assert not SiteSettings.objects.exists()

    response = client.get("/admin/login/")

    assert response.status_code == 200
    assert not SiteSettings.objects.exists(), "an anonymous request must not write"


# --- site settings ---


def test_site_settings_changelist_goes_to_the_single_form(admin: Client) -> None:
    response = admin.get("/admin/il2ks_db/sitesettings/")

    assert response.status_code == 302
    assert response["Location"] == settings_url()
    assert admin.get(settings_url()).status_code == 200


def test_site_settings_cannot_be_added_or_deleted(admin: Client) -> None:
    get_site_settings()

    assert admin.get("/admin/il2ks_db/sitesettings/add/").status_code == 403
    assert admin.get("/admin/il2ks_db/sitesettings/1/delete/").status_code == 403
    assert (
        admin.post("/admin/il2ks_db/sitesettings/", {"action": "delete_selected", "_selected_action": 1}).status_code
        == 302
    )
    assert SiteSettings.objects.filter(pk=1).exists()


def test_saving_site_settings(admin: Client, media_root: Path) -> None:
    get_site_settings()
    before = current_data_version()

    response = admin.post(settings_url(), settings_form())

    assert response.status_code == 302, getattr(response, "context", None) and response.context["adminform"].form.errors
    row = SiteSettings.objects.get(pk=1)
    assert row.site_title == "Korea Fighters"
    assert row.server_name == "Fighter Server"
    assert row.accent_color == "#1A73E8"
    assert row.links == [
        {"label": "Discord", "url": "https://discord.gg/example"},
        {"label": "Homepage", "url": "http://example.org/"},
    ]
    assert (row.redfor_name, row.blufor_name) == ("Red", "Blue")
    assert current_data_version() > before


def test_the_form_shows_the_links_one_per_line(admin: Client) -> None:
    row = get_site_settings()
    row.links = [{"label": "Discord", "url": "https://discord.gg/example"}]
    row.save()

    body = admin.get(settings_url()).content.decode()

    assert "Discord | https://discord.gg/example" in body


@pytest.mark.parametrize("colour", ["red", "#12345", "#1234567", "#GGGGGG", "123456", "#12 456"])
def test_bad_accent_colours_are_refused(admin: Client, colour: str) -> None:
    get_site_settings()
    before = current_data_version()

    response = admin.post(settings_url(), settings_form(accent_color=colour))

    assert response.status_code == 200
    assert "accent_color" in response.context["adminform"].form.errors
    assert SiteSettings.objects.get(pk=1).accent_color == ""
    assert current_data_version() == before


def test_an_empty_accent_colour_means_the_default_theme(admin: Client) -> None:
    get_site_settings()

    assert admin.post(settings_url(), settings_form(accent_color="")).status_code == 302
    assert SiteSettings.objects.get(pk=1).accent_color == ""


@pytest.mark.parametrize(
    "links",
    [
        "javascript:alert(1)",
        "Click | javascript:alert(1)",
        "Click | JaVaScRiPt:alert(1)",
        "Click | data:text/html,<script>alert(1)</script>",
        "Files | ftp://example.org/",
        "Mail | mailto:someone@example.org",
        "Relative | /admin/",
        "Protocol relative | //example.org/",
        "No host | https://",
        "Spaces | https://example.org/a b",
        " | https://example.org/",
        "Label |",
        "\n".join(f"L{i} | https://example.org/{i}" for i in range(11)),
        "X" * 70 + " | https://example.org/",
    ],
)
def test_bad_links_are_refused(admin: Client, links: str) -> None:
    get_site_settings()

    response = admin.post(settings_url(), settings_form(links_text=links))

    assert response.status_code == 200
    assert "links_text" in response.context["adminform"].form.errors
    assert SiteSettings.objects.get(pk=1).links == []


def test_blank_links_clear_the_list(admin: Client) -> None:
    row = get_site_settings()
    row.links = [{"label": "x", "url": "https://example.org/"}]
    row.save()

    assert admin.post(settings_url(), settings_form(links_text="  \n\n")).status_code == 302
    assert SiteSettings.objects.get(pk=1).links == []


# --- logo ---


def upload(data: bytes, name: str = "logo.png", content_type: str = "image/png") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data, content_type=content_type)


def test_uploading_a_logo_stores_a_reencoded_copy_and_serves_it(admin: Client, media_root: Path) -> None:
    get_site_settings()

    response = admin.post(settings_url(), settings_form(logo_upload=upload(png())))

    assert response.status_code == 302
    logo = SiteSettings.objects.get(pk=1).logo
    assert re.fullmatch(r"branding/logo-[0-9a-f]{16}\.png", logo)
    stored = media_root / logo
    assert stored.is_file()
    assert Image.open(stored).size == (120, 40)
    served = admin.get(f"/media/{logo}")
    assert served.status_code == 200
    assert served["X-Content-Type-Options"] == "nosniff"
    assert logo in admin.get(settings_url()).content.decode()  # the form previews it


def test_the_stored_logo_ignores_the_declared_name_and_type(admin: Client, media_root: Path) -> None:
    get_site_settings()
    evil_name = "../../evil.html"

    response = admin.post(settings_url(), settings_form(logo_upload=upload(png(), evil_name, "text/html")))

    assert response.status_code == 302
    logo = SiteSettings.objects.get(pk=1).logo
    assert re.fullmatch(r"branding/logo-[0-9a-f]{16}\.png", logo)
    assert not (media_root.parent / "evil.html").exists()
    assert [p.name for p in (media_root / "branding").iterdir()] == [logo.removeprefix("branding/")]


@pytest.mark.parametrize(
    ("data", "name", "content_type"),
    [
        (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "logo.svg", "image/svg+xml"),
        (b'<svg xmlns="http://www.w3.org/2000/svg"/>', "logo.png", "image/png"),  # SVG renamed to .png
        (b"<html><script>alert(1)</script></html>", "logo.png", "image/png"),
        (b"not an image at all", "logo.png", "image/png"),
        (png()[:60], "logo.png", "image/png"),  # truncated
        (png() + b"\x00" * (2 * 1024 * 1024), "logo.png", "image/png"),  # over the size limit
    ],
    ids=["svg", "svg-as-png", "html", "text", "truncated", "oversized"],
)
def test_bad_logo_uploads_are_refused_and_change_nothing(
    admin: Client, media_root: Path, data: bytes, name: str, content_type: str
) -> None:
    get_site_settings()
    before = current_data_version()

    response = admin.post(settings_url(), settings_form(logo_upload=upload(data, name, content_type)))

    assert response.status_code == 200
    assert "logo_upload" in response.context["adminform"].form.errors
    assert SiteSettings.objects.get(pk=1).logo == ""
    assert SiteSettings.objects.get(pk=1).site_title != "Korea Fighters"  # nothing else was saved either
    assert not media_root.exists() or not list(media_root.rglob("*.*"))
    assert current_data_version() == before


def test_replacing_the_logo_removes_the_old_file(admin: Client, media_root: Path) -> None:
    get_site_settings()
    admin.post(settings_url(), settings_form(logo_upload=upload(png(colour="red"))))
    first = SiteSettings.objects.get(pk=1).logo

    admin.post(settings_url(), settings_form(logo_upload=upload(png(colour="blue"))))
    second = SiteSettings.objects.get(pk=1).logo

    assert first != second
    assert not (media_root / first).exists()
    assert (media_root / second).is_file()


def test_saving_without_a_new_upload_keeps_the_logo(admin: Client, media_root: Path) -> None:
    get_site_settings()
    admin.post(settings_url(), settings_form(logo_upload=upload(png())))
    logo = SiteSettings.objects.get(pk=1).logo

    admin.post(settings_url(), settings_form(site_title="Renamed"))

    row = SiteSettings.objects.get(pk=1)
    assert (row.logo, row.site_title) == (logo, "Renamed")
    assert (media_root / logo).is_file()


def test_removing_the_logo(admin: Client, media_root: Path) -> None:
    get_site_settings()
    admin.post(settings_url(), settings_form(logo_upload=upload(png())))
    logo = SiteSettings.objects.get(pk=1).logo

    response = admin.post(settings_url(), settings_form(remove_logo="on"))

    assert response.status_code == 302
    assert SiteSettings.objects.get(pk=1).logo == ""
    assert not (media_root / logo).exists()


def test_upload_and_remove_together_is_an_error(admin: Client, media_root: Path) -> None:
    get_site_settings()

    response = admin.post(settings_url(), settings_form(logo_upload=upload(png()), remove_logo="on"))

    assert response.status_code == 200
    assert "remove_logo" in response.context["adminform"].form.errors


# --- players and missions ---


def make_players_and_missions() -> tuple[Player, Player, Mission]:
    saved = save(mission((sortie(0, 1), sortie(1, 2))))
    first, second = Player.objects.order_by("pk")
    return first, second, saved


def test_players_are_listed_and_searchable_by_name_and_uuid(admin: Client) -> None:
    first, second, _ = make_players_and_missions()
    Player.objects.filter(pk=first.pk).update(current_name="Zanzibar", name_lower="zanzibar")

    assert admin.get("/admin/il2ks_db/player/").status_code == 200
    by_name = admin.get("/admin/il2ks_db/player/", {"q": "zanzib"}).context["cl"].result_list
    by_uuid = admin.get("/admin/il2ks_db/player/", {"q": second.account_uuid}).context["cl"].result_list
    assert list(by_name) == [first]
    assert list(by_uuid) == [second]


def test_players_are_searchable_by_an_old_name(admin: Client) -> None:
    first, second, _ = make_players_and_missions()
    PlayerName.objects.create(player=first, name="OldHandle", name_lower="oldhandle", first_seen=NOW, last_seen=NOW)

    found = admin.get("/admin/il2ks_db/player/", {"q": "oldhandle"}).context["cl"].result_list

    assert list(found) == [first]
    assert second not in found


def test_player_form_only_allows_hiding(admin: Client) -> None:
    first, _, _ = make_players_and_missions()
    url = f"/admin/il2ks_db/player/{first.pk}/change/"
    form = admin.get(url).context["adminform"].form
    assert list(form.fields) == ["is_hidden"]
    before = current_data_version()

    response = admin.post(url, {"is_hidden": "on", "current_name": "Hacked", "kills_air": "999", "elo_prop": "9"})

    assert response.status_code == 302
    first.refresh_from_db()
    assert first.is_hidden
    assert first.current_name != "Hacked"
    assert first.kills_air != 999
    assert current_data_version() > before


def test_players_and_missions_cannot_be_added_or_deleted(admin: Client) -> None:
    first, _, saved = make_players_and_missions()

    for model, obj in (("player", first), ("mission", saved)):
        assert admin.get(f"/admin/il2ks_db/{model}/add/").status_code == 403
        assert admin.get(f"/admin/il2ks_db/{model}/{obj.pk}/delete/").status_code == 403
        listing = admin.get(f"/admin/il2ks_db/{model}/").content.decode()
        assert "delete_selected" not in listing
    assert Player.objects.filter(pk=first.pk).exists()
    assert Mission.objects.filter(pk=saved.pk).exists()


def test_hide_and_unhide_actions_for_players(admin: Client) -> None:
    first, second, _ = make_players_and_missions()
    before = current_data_version()

    admin.post("/admin/il2ks_db/player/", {"action": "hide_selected", "_selected_action": [first.pk]})

    assert list(Player.objects.visible()) == [second]
    assert list(Player.objects.hidden()) == [first]
    hidden_version = current_data_version()
    assert hidden_version > before

    admin.post("/admin/il2ks_db/player/", {"action": "unhide_selected", "_selected_action": [first.pk, second.pk]})

    assert Player.objects.visible().count() == 2
    assert current_data_version() > hidden_version


def test_hide_and_unhide_actions_for_missions(admin: Client) -> None:
    _, _, saved = make_players_and_missions()
    before = current_data_version()

    admin.post("/admin/il2ks_db/mission/", {"action": "hide_selected", "_selected_action": [saved.pk]})

    assert Mission.objects.visible().count() == 0
    assert current_data_version() > before
    admin.post("/admin/il2ks_db/mission/", {"action": "unhide_selected", "_selected_action": [saved.pk]})
    assert Mission.objects.visible().count() == 1


def test_mission_search_and_form(admin: Client) -> None:
    _, _, saved = make_players_and_missions()
    url = f"/admin/il2ks_db/mission/{saved.pk}/change/"

    found = admin.get("/admin/il2ks_db/mission/", {"q": saved.mission_uid[:10]}).context["cl"].result_list
    nothing = admin.get("/admin/il2ks_db/mission/", {"q": "no-such-mission"}).context["cl"].result_list

    assert list(found) == [saved]
    assert list(nothing) == []
    assert list(admin.get(url).context["adminform"].form.fields) == ["is_hidden"]
    assert admin.post(url, {"is_hidden": "on", "players_total": "77"}).status_code == 302
    saved.refresh_from_db()
    assert saved.is_hidden
    assert saved.players_total != 77


def test_visible_managers_exclude_hidden_rows() -> None:
    first, second, saved = make_players_and_missions()
    Player.objects.filter(pk=first.pk).update(is_hidden=True)
    Mission.objects.filter(pk=saved.pk).update(is_hidden=True)

    assert list(Player.objects.visible()) == [second]
    assert Mission.objects.visible().count() == 0
    assert Player.objects.all().count() == 2
    assert list(Player.objects.filter(pk=second.pk).visible()) == [second]  # chainable on a queryset too


# --- game objects and countries ---


def test_game_object_admin_edits_only_the_display_name(admin: Client) -> None:
    obj = GameObject.objects.create(
        log_name="Mystery-1", display_name="Mystery-1", cls=ObjectClass.UNKNOWN, is_known=False
    )
    url = f"/admin/il2ks_db/gameobject/{obj.pk}/change/"
    assert list(admin.get(url).context["adminform"].form.fields) == ["display_name"]
    before = current_data_version()

    response = admin.post(url, {"display_name": "Mystery Jet", "cls": "fighter", "is_known": "on", "log_name": "x"})

    assert response.status_code == 302
    obj.refresh_from_db()
    assert obj.display_name == "Mystery Jet"
    assert (obj.log_name, obj.cls, obj.is_known) == ("Mystery-1", ObjectClass.UNKNOWN, False)
    assert current_data_version() > before
    assert admin.get("/admin/il2ks_db/gameobject/add/").status_code == 403


def test_game_object_filters_find_unknown_objects(admin: Client) -> None:
    GameObject.objects.create(log_name="Known-1", display_name="Known", cls=ObjectClass.FIGHTER, is_known=True)
    GameObject.objects.create(log_name="Unknown-1", display_name="Unknown-1", cls=ObjectClass.UNKNOWN, is_known=False)

    unknown = admin.get("/admin/il2ks_db/gameobject/", {"is_known__exact": "0"}).context["cl"].result_list
    fighters = admin.get("/admin/il2ks_db/gameobject/", {"cls__exact": "fighter"}).context["cl"].result_list
    searched = admin.get("/admin/il2ks_db/gameobject/", {"q": "unknown"}).context["cl"].result_list

    assert [o.log_name for o in unknown] == ["Unknown-1"]
    assert [o.log_name for o in fighters] == ["Known-1"]
    assert [o.log_name for o in searched] == ["Unknown-1"]


def test_game_object_names_can_be_reset_to_the_catalog(admin: Client) -> None:
    default = load_default_catalog().lookup("F-86A-5").display_name
    assert default
    obj = GameObject.objects.create(log_name="F-86A-5", display_name="My Sabre", cls=ObjectClass.FIGHTER)
    before = current_data_version()

    admin.post("/admin/il2ks_db/gameobject/", {"action": "reset_names", "_selected_action": [obj.pk]})

    obj.refresh_from_db()
    assert obj.display_name == default
    assert current_data_version() > before


def test_country_names_can_be_edited_and_reset(admin: Client) -> None:
    country = Country.objects.create(code=501, coalition=1, display_name="Home-made")
    url = f"/admin/il2ks_db/country/{country.pk}/change/"
    assert list(admin.get(url).context["adminform"].form.fields) == ["display_name"]
    before = current_data_version()

    assert admin.post(url, {"display_name": "Red Land", "code": "999"}).status_code == 302
    country.refresh_from_db()
    assert (country.display_name, country.code) == ("Red Land", 501)
    assert current_data_version() > before

    admin.post("/admin/il2ks_db/country/", {"action": "reset_names", "_selected_action": [country.pk]})
    country.refresh_from_db()
    assert country.display_name == load_default_catalog().country_name(501, 1)


# --- ingestion runs and the status page ---


def run(uid: str, status: str = IngestStatus.OK, *, minutes: int = 0, **fields: object) -> IngestRun:
    return IngestRun.objects.create(
        mission_uid=uid,
        fingerprint=f"fp-{uid}-{minutes}",
        status=status,
        started_at=NOW + timedelta(minutes=minutes),
        finished_at=NOW + timedelta(minutes=minutes, seconds=5),
        **fields,
    )


def test_ingest_runs_are_read_only(admin: Client) -> None:
    ingested = run(
        "2026-10-01_10-00-00", warnings=["something odd"], unknown_atypes={"99": 3}, unknown_keys={"3:ZZ": 2}
    )

    assert admin.get("/admin/il2ks_db/ingestrun/add/").status_code == 403
    assert admin.get(f"/admin/il2ks_db/ingestrun/{ingested.pk}/delete/").status_code == 403
    assert admin.post(f"/admin/il2ks_db/ingestrun/{ingested.pk}/change/", {"status": "failed"}).status_code == 403
    ingested.refresh_from_db()
    assert ingested.status == IngestStatus.OK


def test_ingest_run_detail_shows_warnings_unknowns_and_the_error(admin: Client) -> None:
    failed = run(
        "2026-10-01_10-00-00",
        IngestStatus.FAILED,
        warnings=["clock skew <b>detected</b>"],
        unknown_atypes={"99": 3},
        unknown_keys={"3:ZZ": 2},
        error="Traceback (most recent call last):\n  File x\nValueError: boom",
        files=["missionReport(2026-10-01_10-00-00)[0].txt"],
    )

    body = admin.get(f"/admin/il2ks_db/ingestrun/{failed.pk}/change/").content.decode()

    assert "ValueError: boom" in body
    assert "clock skew &lt;b&gt;detected&lt;/b&gt;" in body, "warnings are escaped"
    assert "3:ZZ" in body
    assert "99" in body
    assert "missionReport(2026-10-01_10-00-00)[0].txt" in body


def test_ingest_run_list_filters_and_search(admin: Client) -> None:
    run("2026-10-01_10-00-00", minutes=0)
    run("2026-10-02_10-00-00", IngestStatus.FAILED, minutes=1, completion_reason=CompletionReason.IDLE)

    def uids(**query: str) -> list[str]:
        response = admin.get("/admin/il2ks_db/ingestrun/", query)
        assert response.status_code == 200
        return [r.mission_uid for r in response.context["cl"].result_list]

    assert uids() == ["2026-10-02_10-00-00", "2026-10-01_10-00-00"]
    assert uids(status__exact="failed") == ["2026-10-02_10-00-00"]
    assert uids(completion_reason__exact="idle") == ["2026-10-02_10-00-00"]
    assert uids(q="10-01") == ["2026-10-01_10-00-00"]
    later = (NOW + timedelta(seconds=30)).isoformat()
    assert uids(started_at__gte=later) == ["2026-10-02_10-00-00"]


def test_status_page_when_nothing_was_ingested(admin: Client) -> None:
    response = admin.get("/admin/ingestion/")

    assert response.status_code == 200
    assert "Nothing ingested yet" in response.content.decode()


def test_status_page_sections(admin: Client) -> None:
    run("2026-10-01_10-00-00", minutes=0)
    run("2026-10-02_10-00-00", completion_reason=CompletionReason.IDLE, minutes=1, unknown_atypes={"99": 3})
    run("2026-10-02_11-00-00", minutes=2, unknown_atypes={"99": 2}, unknown_keys={"3:ZZ": 4})
    # waiting for a retry
    run(
        "2026-10-03_08-00-00",
        IngestStatus.FAILED,
        minutes=3,
        attempts=1,
        next_retry_at=NOW + timedelta(hours=1),
        error="E-RETRY",
    )
    # given up
    run("2026-10-03_09-00-00", IngestStatus.FAILED, minutes=4, attempts=4, next_retry_at=None, error="E-GAVE-UP")
    # failed first, then fixed: not listed
    run("2026-10-03_10-00-00", IngestStatus.FAILED, minutes=5, next_retry_at=NOW, error="E-FIXED")
    run("2026-10-03_10-00-00", IngestStatus.OK, minutes=6)
    GameObject.objects.create(
        log_name="Weird-Plane", display_name="Weird-Plane", cls=ObjectClass.UNKNOWN, is_known=False
    )
    GameObject.objects.create(log_name="Normal-Plane", display_name="Normal", cls=ObjectClass.FIGHTER, is_known=True)
    status = admin.get("/admin/ingestion/")
    body = status.content.decode()
    overview = status.context["overview"]

    assert status.status_code == 200
    assert overview.last_ok.mission_uid == "2026-10-03_10-00-00"
    assert [r.mission_uid for r in overview.waiting_retry] == ["2026-10-03_08-00-00"]
    assert [r.mission_uid for r in overview.gave_up] == ["2026-10-03_09-00-00"]
    assert "E-RETRY" in body
    assert "E-GAVE-UP" in body
    assert "E-FIXED" not in body
    assert "Weird-Plane" in body
    assert "Normal-Plane" not in body
    assert [(u.name, u.count, u.missions) for u in overview.unknown_atypes] == [("99", 5, 2)]
    assert [(u.name, u.count, u.missions) for u in overview.unknown_keys] == [("3:ZZ", 4, 1)]
    assert [r.mission_uid for r in overview.idle_completions] == ["2026-10-02_10-00-00"]
    assert "2026-10-02_10-00-00" in body
