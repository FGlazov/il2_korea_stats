"""Removed UTC filters stay as aliases so old `custom/` overrides keep rendering (TD-25, FR-WEB-17)."""

from datetime import UTC, datetime

from django.template import Context, Template

AT = datetime(2026, 9, 19, 22, 34, tzinfo=UTC)


def render(source: str) -> str:
    return Template(source).render(Context({"at": AT}))


def test_utc_date_is_an_alias_of_local_date() -> None:
    assert render("{% load il2ks %}{{ at|utc_date }}") == render("{% load il2ks %}{{ at|local_date }}")


def test_utc_short_is_an_alias_of_local_short() -> None:
    assert render("{% load il2ks_sorties %}{{ at|utc_short }}") == render("{% load il2ks %}{{ at|local_short }}")
