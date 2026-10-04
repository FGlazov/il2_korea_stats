"""Every template must compile: an unknown filter or tag, a bad `{% load %}` or a syntax error fails here, in one fast
test, instead of only on the page that happens to use it (a template once used the removed `utc_date` filter and only
the page tests noticed). Literal `{% include %}` / `{% extends %}` targets must exist too."""

import re
from pathlib import Path

import pytest
from django.template import TemplateDoesNotExist, TemplateSyntaxError
from django.template.engine import Engine
from django.template.loader import get_template

TEMPLATES = Path(__file__).resolve().parents[2] / "src" / "il2ks" / "web" / "templates"
NAMES = sorted(p.relative_to(TEMPLATES).as_posix() for p in TEMPLATES.rglob("*.html"))
REFERENCE = re.compile(r"{%\s*(?:include|extends)\s+(['\"])(?P<name>[^'\"]+)\1")


def test_templates_exist() -> None:
    assert len(NAMES) > 20


@pytest.mark.parametrize("name", NAMES)
def test_template_compiles(name: str) -> None:
    try:
        get_template(name)
    except TemplateSyntaxError as exc:
        pytest.fail(f"{name} does not compile (unknown filter/tag, missing {{% load %}}, bad syntax): {exc}")


@pytest.mark.parametrize("name", NAMES)
def test_included_and_extended_templates_exist(name: str) -> None:
    source = (TEMPLATES / name).read_text(encoding="utf-8")
    for match in REFERENCE.finditer(source):
        target = match.group("name")
        try:
            get_template(target)
        except TemplateDoesNotExist:
            pytest.fail(f"{name} includes/extends {target!r}, which does not exist")


def test_the_check_catches_an_unknown_filter() -> None:
    engine = Engine(libraries={})
    with pytest.raises(TemplateSyntaxError, match="Invalid filter"):
        engine.from_string("{{ value|utc_date }}")


def test_the_check_catches_an_unknown_tag() -> None:
    engine = Engine(libraries={})
    with pytest.raises(TemplateSyntaxError, match="Invalid block tag"):
        engine.from_string("{% no_such_tag %}")
