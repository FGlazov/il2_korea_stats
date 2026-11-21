"""Markdown pages (roadmap 0.2.0, OQ-133): the renderer is CommonMark and the sanitiser keeps nothing dangerous."""

import pytest

from il2ks.web.pages import render_markdown


def test_commonmark_basics() -> None:
    html = render_markdown(
        "# Rules\n\nBe *kind*, **fly** `well`.\n\n- one\n- two\n\n[forum](https://forum.example/x)\n"
    )
    assert "<h1>Rules</h1>" in html
    assert "<em>kind</em>" in html
    assert "<strong>fly</strong>" in html
    assert "<code>well</code>" in html
    assert "<li>one</li>" in html
    assert 'href="https://forum.example/x"' in html


def test_script_tags_and_their_content_are_dropped() -> None:
    html = render_markdown("Hi\n\n<script>alert(1)</script>\n\n<style>body{display:none}</style>")
    assert "script" not in html
    assert "alert" not in html
    assert "display:none" not in html


@pytest.mark.parametrize(
    "target", ["javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html;base64,PHNjcmlwdD4=", "vbscript:x"]
)
def test_links_with_other_schemes_lose_their_address(target: str) -> None:
    """Markdown refuses to make a link of these (they stay literal text); a raw `<a href>` loses its address."""
    assert "href" not in render_markdown(f"[click]({target})")
    raw = render_markdown(f'<a href="{target}">raw</a>')
    assert "href" not in raw
    assert ">raw</a>" in raw


def test_event_handler_and_style_attributes_are_dropped() -> None:
    html = render_markdown(
        '<p onclick="x()" style="color:red" id="a" class="b">text</p><img src="https://i.example/a.png" onerror="x()">'
    )
    assert "onclick" not in html
    assert "onerror" not in html
    assert "style=" not in html
    assert 'id="a"' not in html
    assert "<p>text</p>" in html


def test_data_urls_in_images_are_dropped() -> None:
    html = render_markdown("![x](data:image/png;base64,AAAA)")
    assert "data:" not in html
    assert "src" not in html


def test_images_may_be_hotlinked_and_do_not_leak_the_referrer() -> None:
    html = render_markdown("![A map](https://img.example/map.png)")
    assert 'src="https://img.example/map.png"' in html
    assert 'alt="A map"' in html
    assert 'referrerpolicy="no-referrer"' in html
    assert 'loading="lazy"' in html


def test_relative_links_are_kept_and_external_ones_do_not_pass_the_referrer_on() -> None:
    html = render_markdown("[home](/) [out](https://x.example/)")
    assert 'href="/"' in html
    assert 'rel="noopener noreferrer"' in html


def test_other_tags_are_stripped_but_their_text_stays() -> None:
    html = render_markdown(
        '<iframe src="https://evil.example"></iframe><form action="/x"><input name=a></form><b>bold</b>'
    )
    assert "iframe" not in html
    assert "<form" not in html
    assert "<input" not in html
    assert "<b>bold</b>" in html


def test_tables_work() -> None:
    html = render_markdown("| a | b |\n|---|---|\n| 1 | 2 |\n")
    assert "<table>" in html
    assert "<td>1</td>" in html


@pytest.mark.parametrize(
    "payload",
    [
        # ammonia 4.2.5: mutation XSS from combining MathML and SVG (nh3 0.3.8, release 0.7.7)
        '<math><mtext><svg><style><img src=x onerror="alert(1)"></style></svg></mtext></math>',
        '<svg><mtext><math><annotation-xml encoding="text/html"><img src=x onerror="alert(1)"></annotation-xml></math>',
        # ammonia 4.1.3 / CVE-2026-63430: `annotation-xml` encoding strip
        '<math><annotation-xml encoding="TEXT/HTML"><script>alert(1)</script></annotation-xml></math>',
        # ammonia 4.2.3 / 4.2.4: CSS url() escaping and case
        '<p style="background:URL(&quot;javascript:alert(1)&quot;)">x</p>',
    ],
)
def test_mathml_and_svg_mutation_xss_payloads_leave_nothing_active(payload: str) -> None:
    """Neither `<svg>` nor `<math>` is on the allowlist, and the sanitiser (nh3, carrying ammonia) is kept at a version
    with the mutation-XSS fixes of ammonia 4.2.3..4.2.5. Whatever shape the payload has, nothing that a browser could
    re-parse into a script or a handler survives."""
    html = render_markdown(payload)
    for forbidden in ("<svg", "<math", "<style", "<script", "onerror", "annotation-xml", "javascript:", "style="):
        assert forbidden not in html.lower(), (forbidden, html)
