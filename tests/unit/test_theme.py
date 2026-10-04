"""Colour themes, fonts and the URL validator (TD-25): the token list matches the stylesheet, no CSS can be injected,
contrast warnings, presets."""

import re
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError

from il2ks.db.validators import NAV_URL_MAX_LENGTH, validate_http_url
from il2ks.web.theme import (
    BODY_FONTS,
    HEADING_FONTS,
    MODES,
    PRESETS,
    TOKEN_KEYS,
    TOKENS,
    Token,
    clean_theme,
    contrast_ratio,
    contrast_warnings,
    effective,
    theme_css,
)

CSS_DIR = Path(__file__).resolve().parents[2] / "src" / "il2ks" / "web" / "static" / "il2ks"
SITE_CSS = (CSS_DIR / "site.css").read_text(encoding="utf-8")
HEX = re.compile(r"#[0-9A-Fa-f]{3,8}\b")


def root_block() -> str:
    start = SITE_CSS.index(":root {")
    return SITE_CSS[start : SITE_CSS.index("\n}\n", start)]


def css_value(var: str) -> str:
    match = re.search(
        rf"^\s*{re.escape(var)}:\s*(.+?);[ \t]*(?:/\*.*?\*/)?[ \t]*$", root_block(), re.MULTILINE | re.DOTALL
    )
    assert match, f"{var} is not defined in site.css :root"
    return " ".join(match.group(1).split())


# --- the registry and the stylesheet agree ---------------------------------------------------------------------


@pytest.mark.parametrize("token", TOKENS, ids=lambda t: t.key)
def test_token_defaults_match_the_stylesheet(token: Token) -> None:
    value = css_value(token.css_var)
    if token.light is None or token.dark is None:
        assert token.light is None
        assert token.dark is None
        return
    expected = token.light if token.light == token.dark else f"light-dark({token.light}, {token.dark})"
    assert value.lower() == expected.lower()


def test_every_colour_in_the_css_is_a_token() -> None:
    """Hex colours may only appear in the `:root` token block; elsewhere the CSS has to use `var(--il2-...)`. (Black
    overlays like `rgb(0 0 0 / .3)` are neutral shading, not theme colours.)"""
    stripped = SITE_CSS.replace(root_block(), "")
    sources = {"site.css": stripped} | {
        path.name: path.read_text(encoding="utf-8") for path in CSS_DIR.glob("*.css") if path.name != "site.css"
    }
    offenders: dict[str, list[str]] = {}
    for name, text in sources.items():
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        found = [m for m in HEX.findall(text) if not m.startswith("#main")]
        found += re.findall(r"light-dark\(", text)
        found += [m for m in re.findall(r"rgb\([^)]*\)", text) if not m.startswith("rgb(0 0 0")]
        if found:
            offenders[name] = found
    assert not offenders, f"colours outside the :root tokens (add a token and use var()): {offenders}"


# `:root` variables that hold a colour but are mixed from tokens (no colour literal of their own): the admin changes
# them through their base token.
DERIVED_COLOURS = {"--il2-shadow-color", "--il2-band-hover", "--il2-band-border"}


def test_every_colour_variable_in_the_root_block_is_a_token() -> None:
    """FR-ADM-2 / TD-25: the admin can edit every colour. A `--il2-*` variable in `:root` whose value is a colour
    (hex, rgb(), light-dark()) must be in `TOKENS`; a mix of tokens (`color-mix`, no literal) may be listed in
    `DERIVED_COLOURS`. Images (`url(...)`) and sizes are not colours."""
    css = re.sub(r"/\*.*?\*/", "", root_block(), flags=re.DOTALL)
    token_vars = {token.css_var for token in TOKENS}
    missing: list[str] = []
    for name, value in re.findall(r"(--il2-[a-z0-9-]+):\s*(.+?);", css, flags=re.DOTALL):
        is_colour = bool(HEX.search(value) or re.search(r"rgba?\(|light-dark\(|color-mix\(", value))
        if not is_colour or name in token_vars:
            continue
        if name in DERIVED_COLOURS:
            assert not HEX.search(value), f"{name} has its own colour literal: make it a token"
            continue
        missing.append(name)
    assert not missing, (
        f"colours in :root that the theme editor cannot change (add them to web.theme.TOKENS): {missing}"
    )
    assert set(re.findall(r"(--il2-[a-z0-9-]+):", css)) >= DERIVED_COLOURS, "stale DERIVED_COLOURS entry"


def test_token_keys_are_unique_and_css_vars_exist() -> None:
    assert len(TOKEN_KEYS) == len(TOKENS)
    for token in TOKENS:
        css_value(token.css_var)


# --- the generated <style> rule --------------------------------------------------------------------------------


def test_default_theme_emits_nothing() -> None:
    assert theme_css({}) == ""
    assert theme_css({"light": {}, "dark": {}}, "", "") == ""
    assert theme_css(None) == ""


def test_overrides_become_light_dark_pairs_and_accent_gets_a_contrast_colour() -> None:
    css = theme_css({"light": {"bg": "#ffffff"}, "dark": {"bg": "#000000", "accent": "#ffd400"}})
    assert "--il2-bg:light-dark(#ffffff, #000000);" in css
    assert "--il2-accent:light-dark(#a56814, #ffd400);" in css  # the light mode keeps its default
    assert "--il2-accent-contrast:light-dark(#ffffff, #10161c);" in css  # light accent gets dark text
    assert css.startswith(":root{")
    assert css.endswith("}")


def test_a_link_override_keeps_the_derived_colour_in_the_other_mode() -> None:
    css = theme_css({"light": {"link": "#0000ee"}})
    assert "--il2-primary:light-dark(#0000ee, color-mix(in srgb, var(--il2-accent) 52%, white));" in css


@pytest.mark.parametrize(
    "evil",
    [
        "red",
        "#12345",
        "#1234567",
        "#12345g",
        "javascript:alert(1)",
        "#aabbcc}body{x:y",
        " #aabbcc",
        "#aabbcc;x:y",
        "url(//x)",
    ],
)
def test_nothing_but_exact_hex_values_reach_the_css(evil: str) -> None:
    assert theme_css({"light": {"bg": evil, "accent": evil}, "dark": {"text": evil}}) == ""
    assert clean_theme({"light": {"bg": evil}}) == {"light": {}, "dark": {}}


def test_unknown_tokens_and_wrong_shapes_are_dropped() -> None:
    assert clean_theme({"light": {"nope": "#ffffff", "}": "#ffffff"}, "dark": ["#fff"], "x": {}}) == {
        "light": {},
        "dark": {},
    }
    assert clean_theme("x") == {"light": {}, "dark": {}}
    assert clean_theme({"light": {"bg": 5}}) == {"light": {}, "dark": {}}


def test_fonts_are_picked_by_key_from_the_fixed_list() -> None:
    css = theme_css({}, "serif", "mono")
    assert f"--il2-font-display:{HEADING_FONTS['serif'][1]};" in css
    assert f"--pico-font-family:{BODY_FONTS['mono'][1]};" in css
    assert theme_css({}, "Comic Sans", "x;}</style>") == ""
    assert theme_css({}, "", "") == ""
    assert "<" not in css
    assert "\\" not in css


def test_font_stacks_contain_only_safe_characters() -> None:
    for fonts in (HEADING_FONTS, BODY_FONTS):
        for _label, stack in fonts.values():
            assert re.fullmatch(r"""[A-Za-z0-9 ,"\-.]*""", stack), stack


# --- contrast --------------------------------------------------------------------------------------------------


def test_contrast_ratio_matches_the_wcag_extremes() -> None:
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast_ratio("#777777", "#777777") == pytest.approx(1.0)


def test_the_shipped_look_has_no_warnings_by_construction() -> None:
    assert contrast_warnings({}) == []


def test_changing_one_colour_checks_its_pairs() -> None:
    found = contrast_warnings({"light": {"text": "#EEEEEE"}})
    assert {(w.mode, w.foreground, w.background) for w in found} >= {
        ("light", "text", "bg"),
        ("light", "text", "surface"),
    }
    assert all(w.mode == "light" for w in found)
    assert all(w.ratio < w.minimum for w in found)


def test_a_pale_accent_warns_about_the_link_colour_and_the_button_text_is_automatic() -> None:
    found = {(w.foreground, w.background) for w in contrast_warnings({"light": {"accent": "#FFFF99"}})}
    assert ("link", "surface") in found
    assert ("accent-contrast", "accent") not in found  # the text colour flips to dark by itself


@pytest.mark.parametrize("key", sorted(PRESETS))
def test_presets_are_valid_and_readable(key: str) -> None:
    _label, theme = PRESETS[key]
    assert clean_theme(theme) == theme  # every value is a known token with a valid colour
    assert contrast_warnings(theme) == []
    # every effective colour of both modes resolves to a plain hex
    for mode in MODES:
        assert all(re.fullmatch(r"#[0-9a-f]{6}", c) for c in effective(clean_theme(theme), mode).values())


# --- the navigation link URL validator -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://discord.gg/abc",
        "http://example.org/",
        "HTTPS://Example.org/Path?x=1#y",
        "https://127.0.0.1:8080/",
        "https://[::1]/",
    ],
)
def test_http_urls_are_accepted(url: str) -> None:
    validate_http_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "javascript:alert(1)",
        "JAVASCRIPT:alert(1)",
        " javascript:alert(1)",
        "java\tscript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:x",
        "file:///etc/passwd",
        "ftp://example.org/",
        "mailto:a@example.org",
        "/relative",
        "//example.org/",
        "https:///nohost",
        "https://",
        "http:example.org",
        "https://exa mple.org/",
        "https://example.org/\n",
        "https://example.org/\x00",
        "https://[bad",
    ],
)
def test_other_addresses_are_refused(url: str) -> None:
    with pytest.raises(ValidationError):
        validate_http_url(url)


def test_a_long_address_is_accepted_up_to_the_column_length() -> None:
    """OQ-87: a link to a detail page may be long; the limit is the NavLink.url column (2000)."""
    validate_http_url("https://example.org/?q=" + "x" * (NAV_URL_MAX_LENGTH - len("https://example.org/?q=")))
    with pytest.raises(ValidationError):
        validate_http_url("https://example.org/?q=" + "x" * NAV_URL_MAX_LENGTH)
