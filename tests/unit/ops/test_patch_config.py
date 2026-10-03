"""`patch_config`: finishing a config that already exists without dropping what the admin added (setup page)."""

import tomllib
from typing import cast

from il2ks.ops.template import fill_template, patch_config, template_text, toml_string


def parse(text: str) -> dict[str, dict[str, object]]:
    return cast(dict[str, dict[str, object]], tomllib.loads(text))


def test_active_lines_are_rewritten_in_place_and_the_rest_is_untouched() -> None:
    text = (
        '# my notes\ndata_dir = "/d"\n\n[https]\nmode = "caddy"  # old\ndomain = "old.example.com"\n\n'
        "[ingest]\nidle_minutes = 7\n"
    )
    out = patch_config(text, {("https", "domain"): toml_string("new.example.com")})
    assert out == text.replace('"old.example.com"', '"new.example.com"')


def test_template_lines_are_switched_on_and_their_table_header_too() -> None:
    out = patch_config(template_text(), {("server", "timezone"): toml_string("Asia/Seoul")})
    assert out == fill_template(template_text(), {("server", "timezone"): toml_string("Asia/Seoul")})


def test_a_missing_key_goes_under_its_header_and_a_missing_table_is_appended() -> None:
    text = 'data_dir = "/d"\n[https]\nmode = "caddy"\n'
    out = patch_config(
        text, {("https", "domain"): '"a.example.com"', ("logs", "dir"): '"/logs"', ("", "log_level"): '"DEBUG"'}
    )
    raw = parse(out)
    assert raw["https"] == {"mode": "caddy", "domain": "a.example.com"}
    assert raw["logs"] == {"dir": "/logs"}
    assert raw["log_level"] == "DEBUG"  # a top-level key lands before the first table, so it stays top-level
    assert raw["data_dir"] == "/d"


def test_a_missing_key_in_a_commented_table_switches_the_table_on() -> None:
    text = '# [https] settings\n#[https]\n# explanation\n#mode = "caddy"\n'
    out = patch_config(text, {("https", "domain"): '"a.example.com"'})
    assert parse(out)["https"] == {"domain": "a.example.com"}


def test_cleared_keys_are_commented_out_and_can_come_back() -> None:
    text = '[https]\ndomain = "old.example.com"\nemail = "me@example.com"\n'
    out = patch_config(text, {}, [("https", "domain"), ("https", "nothing")])
    assert out == '[https]\n#domain = "old.example.com"\nemail = "me@example.com"\n'
    again = patch_config(out, {("https", "domain"): '"x.example.com"'})
    assert parse(again)["https"] == {"domain": "x.example.com", "email": "me@example.com"}


def test_a_key_that_is_both_set_and_cleared_is_set() -> None:
    text = '[https]\ndomain = "old"\n'
    assert parse(patch_config(text, {("https", "domain"): '"new"'}, [("https", "domain")]))["https"] == {
        "domain": "new"
    }


def test_keys_of_other_tables_with_the_same_name_are_not_confused() -> None:
    text = '[logs]\ndir = "/logs"\n[backup]\ndir = "/b"\n'
    out = patch_config(text, {("backup", "dir"): '"/other"'})
    assert parse(out) == {"logs": {"dir": "/logs"}, "backup": {"dir": "/other"}}
