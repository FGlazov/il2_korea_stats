"""Real-world times as <time> elements the browser converts to the viewer's zone (FR-WEB-17, TD-15)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from django.template import Context, Template
from django.utils.safestring import SafeString

from il2ks.web import display

WHEN = datetime(2026, 9, 19, 22, 34, 7, tzinfo=UTC)


def test_the_element_carries_iso_utc_the_kind_and_the_utc_text() -> None:
    html = display.time_element(WHEN, "datetime", suffix=True)
    assert html == '<time datetime="2026-09-19T22:34:07Z" data-il2-time="datetime">2026-09-19 22:34 UTC</time>'
    assert isinstance(html, SafeString)


@pytest.mark.parametrize(
    ("kind", "text"),
    [("datetime", "2026-09-19 22:34"), ("date", "2026-09-19"), ("time", "22:34"), ("clock", "22:34:07")],
)
def test_text_by_kind(kind: display.TimeKind, text: str) -> None:
    expected = f'<time datetime="2026-09-19T22:34:07Z" data-il2-time="{kind}">{text}</time>'
    assert display.time_element(WHEN, kind) == expected


def test_aware_non_utc_and_naive_datetimes_are_normalized_to_utc() -> None:
    tokyo = datetime(2026, 9, 20, 7, 34, 7, tzinfo=timezone(timedelta(hours=9)))
    assert display.time_element(tokyo, "time") == display.time_element(WHEN, "time")
    assert display.time_element(WHEN.replace(tzinfo=None), "time") == display.time_element(WHEN, "time")


def test_none_is_the_dash_not_an_element() -> None:
    assert display.time_element(None) == display.DASH


def test_filters_render_unescaped_in_templates_and_in_blocktranslate() -> None:
    source = (
        "{% load i18n il2ks %}{{ when|local_time }}|{{ when|local_short }}|{{ when|local_date }}|{{ when|local_hm }}"
        "|{{ when|local_clock }}|{% blocktranslate with w=when|local_date %}on {{ w }}{% endblocktranslate %}"
    )
    html = Template(source).render(Context({"when": WHEN}))
    assert html.count("<time ") == 6
    assert "&lt;" not in html
    assert "2026-09-19 22:34 UTC</time>" in html
    assert html.count("data-il2-time=") == 6
