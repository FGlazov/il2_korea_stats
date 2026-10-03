"""The shared web foundation: layout, context processor, components, style guide, error pages (TD-22, TD-24, TD-25)."""

from datetime import UTC, datetime

import pytest
from django.core.paginator import Paginator
from django.db import connection
from django.http import HttpRequest
from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext, override_settings

from il2ks import __version__
from il2ks.db.models import DataVersion, GameObject, SiteSettings
from il2ks.web.context_processors import safe_links, site


def render(source: str, request: HttpRequest | None = None, **context: object) -> str:
    request = request or RequestFactory().get("/")
    return Template("{% load il2ks %}" + source).render(Context({"request": request, **context}))


# --- context processor ---------------------------------------------------------------------------------------------
@pytest.mark.django_db
def test_context_processor_costs_two_reads_and_never_writes() -> None:
    request = RequestFactory().get("/")
    with CaptureQueriesContext(connection) as queries:
        result = site(request)
    assert len(queries) == 2
    assert all(q["sql"].lstrip().upper().startswith("SELECT") for q in queries)
    assert SiteSettings.objects.count() == 0
    assert DataVersion.objects.count() == 0
    row = result["site"]
    assert isinstance(row, SiteSettings)
    assert row.site_title == "IL-2 Korea stats"
    assert result["data_updated"] is None
    assert result["il2ks_version"] == __version__
    assert result["logo_url"] == ""


@pytest.mark.django_db
def test_context_processor_reads_branding_and_data_version() -> None:
    SiteSettings.objects.create(pk=1, site_title="Test Wing", logo="logo/x.png", accent_color="#336699")
    DataVersion.objects.create(pk=1, version=3)
    result = site(RequestFactory().get("/"))
    assert result["logo_url"].endswith("logo/x.png")  # type: ignore[union-attr]
    assert result["accent_css"] == ":root{--il2-accent:#336699;--il2-accent-contrast:#ffffff}"
    assert isinstance(result["data_updated"], datetime)


def test_safe_links_drops_unsafe_schemes_and_junk() -> None:
    raw = [
        {"label": "Discord", "url": "https://discord.example/x"},
        {"label": "Local", "url": "/about/"},
        {"label": "Mail", "url": "mailto:a@example.org"},
        {"label": "Bad", "url": "javascript:alert(1)"},
        {"label": "Data", "url": "data:text/html,x"},
        {"label": "NoUrl"},
        "junk",
        {"label": 5, "url": "https://x.example"},
    ]
    assert safe_links(raw) == [
        ("Discord", "https://discord.example/x"),
        ("Local", "/about/"),
        ("Mail", "mailto:a@example.org"),
    ]
    assert safe_links("not a list") == []


# --- base layout ---------------------------------------------------------------------------------------------------
@pytest.mark.django_db
def test_base_renders_without_a_site_settings_row(client: Client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    html = response.content.decode()
    assert "IL-2 Korea stats" in html
    assert "vendor/htmx.min.js" in html
    assert "vendor/pico.min.css" in html
    assert "Powered by il2ks" in html
    assert "Data updated" not in html
    assert SiteSettings.objects.count() == 0  # a GET never creates the row


@pytest.mark.django_db
def test_base_shows_branding_links_and_data_freshness(client: Client) -> None:
    SiteSettings.objects.create(
        pk=1,
        site_title="Korea Wing",
        server_name="Wing Server One",
        links=[{"label": "Our Discord", "url": "https://discord.example/x"}, {"label": "Evil", "url": "javascript:x"}],
    )
    DataVersion.objects.create(pk=1, version=1)
    html = client.get("/missions/").content.decode()
    assert "Korea Wing" in html
    assert "Wing Server One" in html
    assert "Our Discord" in html
    assert "javascript:x" not in html
    assert "Data updated" in html
    assert 'aria-current="page"' in html


@pytest.mark.django_db
def test_accent_colour_is_injected_only_when_valid(client: Client) -> None:
    SiteSettings.objects.create(pk=1, accent_color="#c0392b")
    assert "--il2-accent:#c0392b" in client.get("/").content.decode()
    # Stored values bypass the form here; they must fit the column (max_length 7): Postgres enforces it.
    for bad in ["red", "#12345", "#a}<b>", "url(x)"]:
        SiteSettings.objects.filter(pk=1).update(accent_color=bad)
        html = client.get("/").content.decode()
        assert "--il2-accent:" not in html
        assert "}<b>" not in html


@pytest.mark.django_db
def test_logo_is_served_from_media_url(client: Client) -> None:
    SiteSettings.objects.create(pk=1, logo="branding/logo.png")
    with override_settings(MEDIA_URL="/media/"):
        html = client.get("/").content.decode()
    assert 'src="/media/branding/logo.png"' in html


# --- style guide and error pages -----------------------------------------------------------------------------------
@pytest.mark.django_db
def test_styleguide_is_404_unless_debug(client: Client) -> None:
    with override_settings(DEBUG=False):
        assert client.get("/_styleguide/").status_code == 404


@pytest.mark.django_db
def test_styleguide_renders_every_component_in_debug(client: Client) -> None:
    with override_settings(DEBUG=True):
        response = client.get("/_styleguide/?aircraft=Yak-9P&sort=-deaths&page=2")
        assert response.status_code == 200
        html = response.content.decode()
        for marker in [
            "stat-tile",
            "badge--redfor",
            "badge--red",
            "pagination",
            "sortable",
            "filter-bar",
            "breadcrumbs",
            "notice--hidden",
            "accordion",
            "dropdown",
            "kv",
        ]:
            assert marker in html
        assert "<svg" in html
        # Unknown sort fields fall back to the default instead of failing.
        assert client.get("/_styleguide/?sort=nonsense;drop").status_code == 200


@pytest.mark.django_db
def test_404_page_uses_the_site_layout() -> None:
    client = Client(raise_request_exception=False)
    with override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"]):
        response = client.get("/definitely/not/here/")
    assert response.status_code == 404
    html = response.content.decode()
    assert "Page not found" in html
    assert "site-header" in html


@pytest.mark.django_db
def test_500_template_needs_no_database_or_context() -> None:
    """The server-error view renders 500.html with an empty context: no site settings, no queries."""
    with CaptureQueriesContext(connection) as queries:
        html = render_to_string("500.html")
    assert len(queries) == 0
    assert "Something went wrong" in html
    assert "site.css" in html


# --- components ----------------------------------------------------------------------------------------------------
def test_sort_th_toggles_direction_and_keeps_other_parameters() -> None:
    request = RequestFactory().get("/p/?aircraft=7&sort=-kills&page=3")
    html = render('{% sort_th "kills" "Kills" numeric=True %}', request, sort="-kills")
    assert 'aria-sort="descending"' in html
    assert "sort=kills" in html
    assert "aircraft=7" in html
    assert "page=" not in html  # sorting starts again at page 1
    fresh = render('{% sort_th "deaths" "Deaths" numeric=True %}{% sort_th "name" "Name" %}', request, sort="-kills")
    assert "sort=-deaths" in fresh  # numeric columns start descending
    assert "sort=name" in fresh
    assert 'aria-sort="none"' in fresh


def test_pagination_links_keep_parameters_and_mark_the_current_page() -> None:
    page = Paginator(list(range(100)), 10).page(5)
    request = RequestFactory().get("/p/?aircraft=7&page=5")
    html = render("{% pagination page_obj %}", request, page_obj=page)
    assert "Showing 41\N{EN DASH}50 of 100" in html
    assert 'aria-current="page"' in html
    assert "aircraft=7" in html
    assert 'rel="prev"' in html
    assert 'rel="next"' in html
    assert 'hx-get="' in html
    first_page = render(
        "{% pagination page_obj %}",
        RequestFactory().get("/p/?aircraft=7"),
        page_obj=Paginator(list(range(100)), 10).page(1),
    )
    assert 'rel="prev"' not in first_page
    assert 'page=1"' not in first_page  # page 1 is the bare URL
    assert (
        render("{% pagination page_obj %}", page_obj=Paginator([], 10).get_page(1)).strip() == ""
    )  # (the template's version line leaves a newline)


def test_filter_select_marks_the_choice_from_the_query_string() -> None:
    request = RequestFactory().get("/p/?aircraft=2&sort=-kills&page=4")
    html = render(
        '{% filter_bar %}{% filter_select "aircraft" "Aircraft" options all_label="All aircraft" %}{% endfilter_bar %}',
        request,
        options=[(1, "Alpha"), (2, "Bravo")],
    )
    assert '<option value="2" selected>Bravo</option>' in html
    assert '<option value="1">Alpha</option>' in html
    assert "All aircraft" in html
    assert '<input type="hidden" name="sort" value="-kills">' in html
    assert "Clear filters" in html
    assert 'name="page"' not in html
    assert "Apply" in html  # the no-JS fallback


def test_filter_bar_hides_clear_link_without_filters() -> None:
    html = render("{% filter_bar %}{% endfilter_bar %}", RequestFactory().get("/p/?sort=-kills&page=2"))
    assert "Clear filters" not in html


def test_results_region_carries_the_htmx_pattern() -> None:
    html = render("{% results_region %}<p>body</p>{% endresults_region %}")
    for attribute in [
        'id="results"',
        'hx-target="#results"',
        'hx-select="#results"',
        'hx-swap="outerHTML"',
        'hx-push-url="true"',
    ]:
        assert attribute in html
    assert "<p>body</p>" in html


def test_badges_render_labels_tones_and_icons() -> None:
    html = render(
        '{% coalition_badge 501 %}{% coalition_badge 601 %}{% outcome_badge "shot_down" %}'
        '{% fate_badge "disconnected" %}{% status_badge "dead" %}{% role_badge "attack" %}'
        '{% aircraft_badge "destroyed" %}{% badge "Hi" "teal" %}',
        site=SiteSettings(redfor_name="Reds", blufor_name="Blues"),
    )
    assert "badge--redfor" in html
    assert "Reds" in html
    assert "Blues" in html
    assert "badge--red" in html
    assert "Shot down" in html
    assert "Left the server" in html
    assert "Destroyed" in html
    assert "badge--teal" in html
    assert "<svg" in html


def test_side_tag_uses_site_names_and_works_with_as() -> None:
    site_row = SiteSettings(redfor_name="Reds", blufor_name="Blues")
    assert render("{% side 601 %}", site=site_row) == "Blues"
    assert render("{% side 501 as who %}[{{ who }}]", site=site_row) == "[Reds]"
    assert render("{% side 0 %}") == "Neutral"


def test_notice_accordion_and_small_components() -> None:
    html = render(
        '{% notice "changing" %}{% endnotice %}{% notice "hidden" title="Custom" %}Own text{% endnotice %}'
        '{% accordion "Details" hint="3 rows" open=True %}inside{% endaccordion %}'
        '{% stat_tile "1,234" "Sorties" sub="87 %" icon="stat/sorties" %}{% kv_list rows %}{% empty_row 4 %}'
        '{% breadcrumbs crumbs %}{% dropdown "Menu" items align="right" %}',
        rows=[("A", "1")],
        crumbs=[("Home", "/"), ("Here", None)],
        items=[("One", "/1/")],
    )
    assert "May still change" in html
    assert "Custom" in html
    assert "Own text" in html
    assert '<details class="accordion" open>' in html
    assert "inside" in html
    assert "stat-tile__icon" in html
    assert "<dt>A</dt>" in html
    assert 'colspan="4"' in html
    assert 'aria-current="page">Here' in html
    assert "dropdown--right" in html


def test_icon_tag_inlines_svg_and_ignores_bad_names() -> None:
    assert render('{% icon "stat/sorties" %}').startswith('<svg class="icon"')
    assert 'class="icon extra"' in render('{% icon "stat/sorties" "extra" %}')
    assert render('{% icon "nope/missing" %}') == ""
    assert render('{% icon "../../settings" %}') == ""
    assert render('{% icon "stat/sorties" "x\\"onload=\\"y" %}') == ""


def test_aircraft_icon_falls_back_by_propulsion() -> None:
    jet = GameObject(log_name="Never-Heard-Of-It", propulsion="jet")
    prop = GameObject(log_name="Never-Heard-Of-It", propulsion="prop")
    unknown = GameObject(log_name="Never-Heard-Of-It", propulsion="")
    assert render("{% aircraft_icon a %}", a=jet) != render("{% aircraft_icon a %}", a=prop)
    assert render("{% aircraft_icon a %}", a=unknown).startswith("<svg")


def test_filters_in_templates() -> None:
    html = render(
        "{{ 4980|duration }}|{{ when|utc }}|{{ 1234567|num }}|{{ 482|ratio:205 }}|{{ 5|ratio:0 }}"
        "|{{ 87|percent:100 }}|{{ 12|per_hour:18000 }}",
        when=datetime(2026, 9, 19, 22, 34, tzinfo=UTC),
    )
    assert html == "1 h 23 min|2026-09-19 22:34 UTC|1,234,567|2.35|—|87%|2.40"
