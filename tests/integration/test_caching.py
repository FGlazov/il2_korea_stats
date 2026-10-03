"""Data-version ETags and the 304 short-circuit (TD-28)."""

import re
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.http import HttpRequest, HttpResponse
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext

from il2ks.db.models import DataVersion
from il2ks.db.site import bump_data_version, current_data_version
from il2ks.web.caching import DataVersionCacheMiddleware, make_etag

pytestmark = pytest.mark.django_db


def _directives(response: HttpResponse) -> set[str]:
    return {d.strip() for d in response["Cache-Control"].split(",")}


def get_page(client: Client, url: str = "/", **headers: str) -> HttpResponse:
    return client.get(url, headers=headers or None)


def test_public_page_gets_etag_and_cache_headers(client: Client) -> None:
    response = get_page(client)

    assert response.status_code == 200
    assert re.fullmatch(r'"[0-9a-f]{32}"', response["ETag"])
    assert not response["ETag"].startswith("W/")
    assert _directives(response) == {"max-age=0", "must-revalidate"}  # TD-28: a hidden player disappears at once
    vary = {v.strip() for v in response["Vary"].split(",")}
    assert {"HX-Request", "Accept-Language"} <= vary


def test_matching_etag_gets_304_without_running_the_view(client: Client) -> None:
    etag = get_page(client)["ETag"]

    with patch("il2ks.web.views.missions.render") as render, CaptureQueriesContext(connection) as queries:
        response = get_page(client, **{"If-None-Match": etag})

    assert response.status_code == 304
    assert response.content == b""
    render.assert_not_called()
    assert len(queries) == 1, "the data version lookup is the only query"
    assert "il2ks_db_dataversion" in queries.captured_queries[0]["sql"]
    assert response["ETag"] == etag
    assert _directives(response) == {"max-age=0", "must-revalidate"}


def test_weak_and_listed_etags_match(client: Client) -> None:
    etag = get_page(client)["ETag"]

    assert get_page(client, **{"If-None-Match": f"W/{etag}"}).status_code == 304
    assert get_page(client, **{"If-None-Match": f'"other", {etag}'}).status_code == 304
    assert get_page(client, **{"If-None-Match": "*"}).status_code == 200  # `*` is for conditional writes (RFC 9110)
    assert get_page(client, **{"If-None-Match": '"other"'}).status_code == 200


def test_head_gets_the_same_treatment(client: Client) -> None:
    etag = get_page(client)["ETag"]

    assert client.head("/", headers={"If-None-Match": etag}).status_code == 304
    assert client.head("/")["ETag"] == etag


def test_data_version_bump_changes_the_etag(client: Client) -> None:
    before = get_page(client)["ETag"]
    assert get_page(client, **{"If-None-Match": before}).status_code == 304

    bump_data_version()

    stale = get_page(client, **{"If-None-Match": before})
    assert stale.status_code == 200
    assert stale["ETag"] != before


def test_etag_varies_with_path_query_language_and_htmx() -> None:
    factory = RequestFactory()
    base = make_etag(factory.get("/players/?q=a"), 1)

    assert make_etag(factory.get("/players/?q=a"), 1) == base
    assert make_etag(factory.get("/players/?q=b"), 1) != base
    assert make_etag(factory.get("/missions/?q=a"), 1) != base
    assert make_etag(factory.get("/players/?q=a", headers={"HX-Request": "true"}), 1) != base
    assert make_etag(factory.get("/players/?q=a"), 2) != base


def test_admin_is_excluded(client: Client) -> None:
    User.objects.create_superuser("boss", "boss@example.org", "x")
    client.login(username="boss", password="x")

    for url in ("/admin/login/", "/admin/", "/admin/il2ks_db/sitesettings/1/change/"):
        response = client.get(url, follow=True)
        assert "ETag" not in response.headers, url

    # even an If-None-Match that cannot match is ignored, and a real admin page is never answered with a 304
    assert client.get("/admin/", headers={"If-None-Match": "*"}).status_code != 304


def test_media_and_static_are_excluded(client: Client) -> None:
    for url in ("/media/branding/nope.png", "/static/nothing.css"):
        response = client.get(url, headers={"If-None-Match": "*"})
        assert response.status_code == 404
        assert "ETag" not in response.headers


def test_non_get_requests_are_untouched(client: Client) -> None:
    response = client.post("/", headers={"If-None-Match": "*"})

    assert response.status_code != 304
    assert "ETag" not in response.headers


def test_missing_pages_get_no_cache_headers(client: Client) -> None:
    response = client.get("/no/such/page/")

    assert response.status_code == 404
    assert "ETag" not in response.headers


def test_version_row_is_created_lazily_and_bumped_in_place() -> None:
    DataVersion.objects.all().delete()
    assert current_data_version() == 0

    bump_data_version()
    bump_data_version()

    assert current_data_version() == 2
    assert DataVersion.objects.count() == 1


# --- the response rules, with a fake downstream so we can make cookies / errors / own headers ---


def run(response: HttpResponse, *, method: str = "GET") -> HttpResponse:
    middleware = DataVersionCacheMiddleware(lambda request: response)
    request: HttpRequest = RequestFactory().generic(method, "/players/")

    def view(request: HttpRequest) -> HttpResponse:
        return response

    assert middleware.process_view(request, view, (), {}) is None
    return middleware(request)


def test_responses_that_set_cookies_are_not_cached() -> None:
    response = HttpResponse("x")
    response.set_cookie("sessionid", "abc")

    assert "ETag" not in run(response).headers


def test_non_200_responses_are_not_cached() -> None:
    assert "ETag" not in run(HttpResponse("x", status=404)).headers
    assert "ETag" not in run(HttpResponse(status=302)).headers


def test_a_view_with_its_own_cache_control_is_left_alone() -> None:
    response = HttpResponse("x", headers={"Cache-Control": "max-age=5"})

    result = run(response)

    assert result["Cache-Control"] == "max-age=5"
    assert "ETag" not in result.headers


def test_a_view_with_its_own_etag_is_left_alone() -> None:
    response = HttpResponse("x", headers={"ETag": '"mine"'})

    assert run(response)["ETag"] == '"mine"'


def test_ok_response_is_decorated() -> None:
    result = run(HttpResponse("x"))

    assert "ETag" in result.headers
    assert _directives(result) == {"max-age=0", "must-revalidate"}
