"""Markdown pages in the navigation (roadmap 0.2.0, OQ-133): the admin saves and renders once, the page view prints the
stored HTML in the viewer's language, a navigation link opens it."""

import pytest
from django.contrib.auth.models import User
from django.test import Client

from il2ks.db.models import NavLink, Page, PageTranslation, SiteSettings
from il2ks.db.site import current_data_version
from il2ks.web.pages import publish_nav_links, publish_page
from tests.simple_reads import assert_simple_reads

pytestmark = pytest.mark.django_db

PAGE_READS = 3  # the site context (settings row, data version) and the page itself


@pytest.fixture
def admin(client: Client) -> Client:
    client.force_login(User.objects.create_superuser("boss", "boss@example.org", "x"))
    return client


def make_page(slug: str = "rules", title: str = "Server rules", source: str = "# Be nice\n\nFly **safe**.") -> Page:
    page = Page.objects.create(slug=slug, title=title, source=source)
    publish_page(page)
    return page


def translate(page: Page, language: str, source: str, title: str = "") -> None:
    PageTranslation.objects.create(page=page, language=language, title=title, source=source)
    publish_page(page)


def page_form(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "title": "Server rules",
        "slug": "rules",
        "base_language": "en",
        "source": "# Be nice\n\nFly **safe**.",
        "page_translations-TOTAL_FORMS": 0,
        "page_translations-INITIAL_FORMS": 0,
        "page_translations-MIN_NUM_FORMS": 0,
        "page_translations-MAX_NUM_FORMS": 6,
    }
    data.update(overrides)
    return data


# --- the public page ---


def test_the_page_shows_its_stored_html_in_the_site_layout(client: Client) -> None:
    make_page()
    response = client.get("/p/rules/")
    html = response.content.decode()
    assert response.status_code == 200
    assert "<h1>Be nice</h1>" in html
    assert "<strong>safe</strong>" in html
    assert "<title>Server rules" in html
    assert 'class="site-nav"' in html  # inside the layout


def test_an_unknown_page_is_a_404(client: Client) -> None:
    assert client.get("/p/nope/").status_code == 404


def test_the_page_view_never_renders_markdown(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    make_page()

    def boom(source: str) -> str:
        raise AssertionError("rendered per request")

    monkeypatch.setattr("il2ks.web.pages.render_markdown", boom)
    assert client.get("/p/rules/").status_code == 200


def test_stored_html_is_what_is_shown_even_if_the_source_changes_underneath(client: Client) -> None:
    """Rendering happens on save only: the source column is not read to answer a request."""
    page = make_page()
    Page.objects.filter(pk=page.pk).update(source="# Other")
    assert "<h1>Be nice</h1>" in client.get("/p/rules/").content.decode()


def test_the_page_view_stays_inside_the_query_budget(client: Client) -> None:
    page = make_page()
    translate(page, "de", "# Sei nett", title="Serverregeln")
    assert_simple_reads(client, "/p/rules/", PAGE_READS)
    client.cookies["django_language"] = "de"
    assert_simple_reads(client, "/p/rules/", PAGE_READS)


def test_the_page_allows_https_images_through_its_content_security_policy(client: Client) -> None:
    make_page(source="![map](https://img.example/map.png)")
    response = client.get("/p/rules/")
    policy = response["Content-Security-Policy"]
    assert "img-src 'self' https:" in policy
    assert "object-src 'none'" in policy
    assert 'src="https://img.example/map.png"' in response.content.decode()


def test_the_page_revalidates_after_a_save_bumps_the_data_version(client: Client) -> None:
    make_page()
    first = client.get("/p/rules/")
    assert first["ETag"]
    assert client.get("/p/rules/", headers={"If-None-Match": first["ETag"]}).status_code == 304


# --- languages ---


def test_the_viewers_language_text_is_shown_when_there_is_one(client: Client) -> None:
    page = make_page()
    translate(page, "de", "# Sei nett", title="Serverregeln")
    client.cookies["django_language"] = "de"
    html = client.get("/p/rules/").content.decode()
    assert "<h1>Sei nett</h1>" in html
    assert "<title>Serverregeln" in html
    assert 'lang="de"' in html
    assert "Be nice" not in html


def test_a_viewer_without_a_translation_gets_the_base_text(client: Client) -> None:
    page = make_page()
    translate(page, "de", "# Sei nett")
    client.cookies["django_language"] = "fr"
    html = client.get("/p/rules/").content.decode()
    assert "<h1>Be nice</h1>" in html
    assert 'lang="en"' in html


def test_a_translation_without_a_title_keeps_the_base_title(client: Client) -> None:
    page = make_page()
    translate(page, "ru", "# Будьте добры")
    client.cookies["django_language"] = "ru"
    html = client.get("/p/rules/").content.decode()
    assert "<h1>Server rules</h1>" in html
    assert "Будьте добры" in html


def test_a_page_without_any_translation_works_in_every_language(client: Client) -> None:
    make_page()
    for code in ("en", "de", "fr", "ru", "es", "pt-br"):
        client.cookies["django_language"] = code
        assert "<h1>Be nice</h1>" in client.get("/p/rules/").content.decode()


# --- the navigation link ---


def link_to(page: Page, label: str = "Rules") -> None:
    site = SiteSettings.objects.get_or_create(pk=1)[0]
    NavLink.objects.create(site=site, label=label, page=page, position=1)
    publish_nav_links(site)


def test_a_nav_link_to_a_page_opens_it_in_the_same_tab(client: Client) -> None:
    link_to(make_page())
    html = client.get("/").content.decode()
    nav = html[html.index('class="site-nav"') : html.index("</nav>")]
    assert '<a href="/p/rules/">Rules</a>' in nav
    assert "target=" not in nav[nav.index("/p/rules/") - 20 : nav.index("/p/rules/") + 40]


def test_the_nav_link_of_the_open_page_is_marked_current(client: Client) -> None:
    link_to(make_page())
    html = client.get("/p/rules/").content.decode()
    assert '<a href="/p/rules/" aria-current="page">Rules</a>' in html


def test_several_pages_each_get_their_own_link_in_order(client: Client) -> None:
    site = SiteSettings.objects.create(pk=1)
    first, second = make_page("rules", "Rules"), make_page("about", "About")
    NavLink.objects.create(site=site, label="About us", page=second, position=1)
    NavLink.objects.create(site=site, label="Discord", url="https://discord.example/x", position=2)
    NavLink.objects.create(site=site, label="Rules", page=first, position=3)
    publish_nav_links(site)
    html = client.get("/").content.decode()
    nav = html[html.index('class="site-nav"') : html.index("</nav>")]
    assert nav.index("/p/about/") < nav.index("discord.example") < nav.index("/p/rules/")


def test_a_hand_edited_page_link_outside_p_is_ignored(client: Client) -> None:
    SiteSettings.objects.create(
        pk=1,
        links=[
            {"label": "Evil", "url": "javascript:alert(1)", "icon": "", "page": "1"},
            {"label": "Other", "url": "//evil.example/p/x", "icon": "", "page": "1"},
            {"label": "Fine", "url": "/p/rules/", "icon": "", "page": "1"},
        ],
    )
    nav = client.get("/").content.decode()
    assert "Evil" not in nav
    assert "Other" not in nav
    assert "Fine" in nav


# --- the admin ---


def test_the_admin_creates_a_page_renders_it_once_and_bumps_the_data_version(admin: Client) -> None:
    before = current_data_version()
    response = admin.post("/admin/il2ks_db/page/add/", page_form())
    assert response.status_code == 302
    page = Page.objects.get(slug="rules")
    assert "<h1>Be nice</h1>" in page.html
    assert current_data_version() > before
    assert "<strong>safe</strong>" in admin.get("/p/rules/").content.decode()


def test_the_admin_sanitises_what_it_stores(admin: Client) -> None:
    source = '<script>alert(1)</script><a href="javascript:y">x</a><b onclick="z">b</b>'
    admin.post("/admin/il2ks_db/page/add/", page_form(source=source))
    page = Page.objects.get(slug="rules")
    assert page.html.strip() == '<a rel="noopener noreferrer">x</a><b>b</b>'


def test_the_admin_stores_translations_and_publishes_them(admin: Client) -> None:
    data = page_form(
        **{
            "page_translations-TOTAL_FORMS": 1,
            "page_translations-0-language": "de",
            "page_translations-0-title": "Serverregeln",
            "page_translations-0-source": "# Sei nett",
        }
    )
    assert admin.post("/admin/il2ks_db/page/add/", data).status_code == 302
    page = Page.objects.get(slug="rules")
    assert page.translations == {"de": {"title": "Serverregeln", "html": "<h1>Sei nett</h1>\n"}}


def test_the_admin_change_form_offers_a_preview(admin: Client) -> None:
    html = admin.get("/admin/il2ks_db/page/add/").content.decode()
    assert 'class="md-preview-button"' in html
    assert "/admin/il2ks_db/page/preview/" in html


def test_the_preview_renders_and_sanitises_without_saving(admin: Client) -> None:
    response = admin.post("/admin/il2ks_db/page/preview/", {"source": "**hi**<script>x</script>"})
    assert response.status_code == 200
    assert response.content.decode().strip() == "<p><strong>hi</strong></p>"
    assert not Page.objects.exists()


def test_the_preview_is_for_staff_only(client: Client) -> None:
    response = client.post("/admin/il2ks_db/page/preview/", {"source": "x"})
    assert response.status_code == 302
    assert "/login/" in response["Location"]


def test_renaming_a_slug_moves_the_nav_link(admin: Client) -> None:
    page = make_page()
    link_to(page)
    admin.post(f"/admin/il2ks_db/page/{page.pk}/change/", page_form(slug="regeln"))
    assert SiteSettings.objects.get(pk=1).links[0]["url"] == "/p/regeln/"


def test_deleting_a_page_removes_its_nav_link(admin: Client) -> None:
    page = make_page()
    link_to(page)
    before = current_data_version()
    response = admin.post(f"/admin/il2ks_db/page/{page.pk}/delete/", {"post": "yes"})
    assert response.status_code == 302
    assert not Page.objects.exists()
    assert not NavLink.objects.exists()
    assert SiteSettings.objects.get(pk=1).links == []
    assert current_data_version() > before


def nav_form(label: str, url: str, page: object) -> dict[str, object]:
    return {
        "site_title": "Korea Fighters",
        "server_name": "Fighter Server",
        "description": "Welcome",
        "redfor_name": "Red",
        "blufor_name": "Blue",
        "redfor_emblem": "plaaf",
        "blufor_emblem": "rokaf",
        "nav_links-TOTAL_FORMS": 1,
        "nav_links-INITIAL_FORMS": 0,
        "nav_links-MIN_NUM_FORMS": 0,
        "nav_links-MAX_NUM_FORMS": 1000,
        "nav_links-0-label": label,
        "nav_links-0-url": url,
        "nav_links-0-page": page,
        "nav_links-0-icon": "",
        "nav_links-0-position": 1,
    }


def test_the_site_settings_admin_links_a_page_and_publishes_its_address(admin: Client) -> None:
    page = make_page()
    SiteSettings.objects.get_or_create(pk=1)
    response = admin.post("/admin/il2ks_db/sitesettings/1/change/", nav_form("Rules", "", page.pk))
    assert response.status_code == 302, response.content.decode()[:3000]
    assert SiteSettings.objects.get(pk=1).links == [{"label": "Rules", "url": "/p/rules/", "icon": "", "page": "1"}]


@pytest.mark.parametrize("both", [True, False])
def test_a_nav_link_needs_exactly_one_target(admin: Client, both: bool) -> None:
    page = make_page()
    SiteSettings.objects.get_or_create(pk=1)
    data = nav_form("X", "https://x.example/" if both else "", page.pk if both else "")
    response = admin.post("/admin/il2ks_db/sitesettings/1/change/", data)
    assert response.status_code == 200
    assert not NavLink.objects.exists()
