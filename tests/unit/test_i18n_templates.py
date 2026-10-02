"""Harness for TD-24: every visible string in our templates is wrapped for translation from day one."""

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[2] / "src" / "il2ks" / "web" / "templates"
TRANSLATED = re.compile(r"{%\s*(blocktranslate|blocktrans)\b.*?{%\s*end\1\s*%}", re.DOTALL)
TAGS = re.compile(r"{%.*?%}|{{.*?}}|{#.*?#}|<[^>]*>", re.DOTALL)


def untranslated_text(source: str) -> list[str]:
    stripped = TRANSLATED.sub(" ", source)
    stripped = TAGS.sub(" ", stripped)
    return [chunk.strip() for chunk in stripped.splitlines() if re.search(r"[A-Za-z]", chunk)]


def template_files() -> list[Path]:
    return sorted(TEMPLATES.rglob("*.html"))


def test_templates_exist() -> None:
    assert template_files()


@pytest.mark.parametrize("path", template_files(), ids=lambda p: p.name)
def test_template_text_is_translated(path: Path) -> None:
    leftovers = untranslated_text(path.read_text(encoding="utf-8"))
    assert not leftovers, f"Wrap these in {{% translate %}} (TD-24): {leftovers}"


def test_harness_catches_bare_text() -> None:
    assert untranslated_text("<p>Hello pilots</p>") == ["Hello pilots"]
    assert untranslated_text('<p>{% translate "Hello" %}</p>') == []
