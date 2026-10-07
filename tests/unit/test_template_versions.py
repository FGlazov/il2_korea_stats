"""Versions of built-in templates and static files (TD-25): header parsing, the registry drift test, `bump-templates`.

The drift test is the one that makes a developer raise a template's version when changing it:
"template X changed: bump its version, run `il2ks dev bump-templates`"."""

import json
import subprocess
from pathlib import Path

import pytest

from il2ks.cli import EXIT_OK, main
from il2ks.devtools import templates as dev_templates
from il2ks.serving import templateversions as tv
from il2ks.serving.templateversions import Version

# --- header parsing --------------------------------------------------------------------------------------------------


def test_html_header() -> None:
    text = "{# il2ks-template: templates/il2ks/base.html v12 - copy this line along when you override #}\n<p>x</p>\n"
    header = tv.find_header(text, "il2ks/base.html")
    assert header == tv.Header("templates/il2ks/base.html", Version(12), 0, minor_given=False)


@pytest.mark.parametrize("name", ["site.css", "il2ks.js"])
def test_css_and_js_header(name: str) -> None:
    text = f"/* il2ks-template: static/il2ks/{name} v3 - copy this line along when you override */\nbody {{}}\n"
    header = tv.find_header(text, name)
    assert header is not None
    assert header.version == Version(3)
    assert header.key == f"static/il2ks/{name}"


def test_header_style_follows_the_file_type() -> None:
    css = "/* il2ks-template: static/a.css v1 - x */"
    assert tv.find_header(css, "a.html") is None  # a template wants {# #}
    assert tv.find_header("{# il2ks-template: templates/a.html v1 #}", "a.css") is None


def test_header_is_found_a_few_lines_down_but_not_in_the_middle_of_a_file() -> None:
    text = "{# my own note #}\n{# il2ks-template: templates/a.html v2 #}\n<p>x</p>"
    assert tv.find_header(text, "a.html") == tv.Header("templates/a.html", Version(2), 1, minor_given=False)
    far = "\n" * tv.HEADER_SCAN_LINES + "{# il2ks-template: templates/a.html v2 #}"
    assert tv.find_header(far, "a.html") is None


def test_header_survives_a_byte_order_mark_and_crlf() -> None:
    text = "﻿{# il2ks-template: templates/a.html v4 - x #}\r\n<p>x</p>\r\n"
    assert tv.find_header(text, "a.html") == tv.Header("templates/a.html", Version(4), 0, minor_given=False)


@pytest.mark.parametrize(
    "text", ["", "<p>hello</p>", "{# il2ks-template: templates/a.html #}", "{# il2ks-template: templates/a.html vX #}"]
)
def test_no_header(text: str) -> None:
    assert tv.find_header(text, "a.html") is None


def test_with_header_adds_replaces_and_keeps_newline_style() -> None:
    added = tv.with_header("<p>x</p>\n", "templates/a.html", Version(1))
    assert added == "{# il2ks-template: templates/a.html v1.0 - copy this line along when you override #}\n<p>x</p>\n"
    replaced = tv.with_header(added, "templates/a.html", Version(7, 2))
    assert replaced.splitlines()[0].startswith("{# il2ks-template: templates/a.html v7.2 ")
    assert replaced.endswith("<p>x</p>\n")
    assert replaced.count("il2ks-template") == 1
    crlf = tv.with_header("<p>x</p>\r\n", "templates/a.html", Version(1))
    assert crlf.count("\r\n") == 2
    assert "\n" not in crlf.replace("\r\n", "")
    css = tv.with_header("a {}\n", "static/a.css", Version(2))
    assert css.startswith("/* il2ks-template: static/a.css v2.0 - ")


def test_the_hash_ignores_the_header_and_line_endings() -> None:
    body = "<p>x</p>\n<p>y</p>\n"
    header, stripped = tv.split_header(tv.with_header(body, "templates/a.html", Version(5)), "a.html")
    assert header is not None
    assert stripped == body
    assert tv.content_hash(stripped) == tv.content_hash(body)
    assert tv.content_hash("a\r\nb\r\n") == tv.content_hash("a\nb\n")
    assert tv.content_hash(body) != tv.content_hash(body + "z")


@pytest.mark.parametrize(
    ("kind", "rel", "expected"),
    [
        ("templates", "il2ks/base.html", True),
        ("static", "il2ks/site.css", True),
        ("static", "il2ks/il2ks.js", True),
        ("static", "il2ks/vendor/htmx.min.js", False),
        ("static", "il2ks/vendor/pico.min.css", False),
        ("static", "il2ks/img/outcome/landed.svg", False),
        ("static", "il2ks/vendor/BarlowCondensed-600.woff2", False),
        ("templates", "il2ks/notes.md", False),
    ],
)
def test_which_files_are_versioned(kind: tv.Kind, rel: str, expected: bool) -> None:
    assert tv.is_versioned(kind, rel) is expected


# --- the drift test over the real files ---------------------------------------------------------------------------


def test_every_built_in_template_and_static_file_is_versioned_and_registered() -> None:
    """If this fails after you changed a template, stylesheet or script: run `il2ks dev bump-templates` and commit
    the result. It raises the file's version (so server owners get warned that their copy is based on an old one),
    and updates src/il2ks/web/template_versions.json."""
    actions = tv.bump_templates(write=False)
    assert not actions, "template versions are out of date: run `il2ks dev bump-templates`\n" + "\n".join(
        f"  {tv.describe(a)}" for a in actions
    )


def test_the_registry_covers_real_files() -> None:
    registry = tv.load_registry()
    files = tv.versioned_files()
    assert registry
    assert set(registry) == set(files)
    assert "templates/il2ks/base.html" in registry
    assert "static/il2ks/site.css" in registry
    assert not any("/vendor/" in key or key.endswith(".svg") for key in registry)


def test_the_registry_file_is_sorted_json_with_a_format_number() -> None:
    document = json.loads(tv.registry_path().read_text(encoding="utf-8"))
    assert document["format"] == tv.REGISTRY_FORMAT
    assert list(document["files"]) == sorted(document["files"])
    assert all(
        tv.parse_version(e["version"]) is not None and len(e["sha256"]) == 64 for e in document["files"].values()
    )
    assert all(isinstance(e.get("contract", {}), dict) for e in document["files"].values())


# --- bump-templates on a scratch copy -----------------------------------------------------------------------------


@pytest.fixture
def web(tmp_path: Path) -> Path:
    root = tmp_path / "web"
    (root / "templates" / "il2ks").mkdir(parents=True)
    (root / "static" / "il2ks" / "vendor").mkdir(parents=True)
    (root / "static" / "il2ks" / "img").mkdir(parents=True)
    (root / "templates" / "il2ks" / "a.html").write_text("<p>a</p>\n", encoding="utf-8")
    (root / "templates" / "il2ks" / "b.html").write_text("<p>b</p>\n", encoding="utf-8")
    (root / "static" / "il2ks" / "site.css").write_text("body {}\n", encoding="utf-8")
    (root / "static" / "il2ks" / "vendor" / "lib.js").write_text("var lib;\n", encoding="utf-8")
    (root / "static" / "il2ks" / "img" / "icon.svg").write_text("<svg/>", encoding="utf-8")
    return root


def test_first_run_adds_headers_at_v1_and_registers_everything_versioned(web: Path) -> None:
    actions = tv.bump_templates(web)
    assert {a.key for a in actions} == {
        "templates/il2ks/a.html",
        "templates/il2ks/b.html",
        "static/il2ks/site.css",
    }
    assert all(a.kind == "add-header" and a.new_version == Version(1) for a in actions)
    assert (
        (web / "templates" / "il2ks" / "a.html")
        .read_text(encoding="utf-8")
        .startswith("{# il2ks-template: templates/il2ks/a.html v1.0 ")
    )
    assert (web / "static" / "il2ks" / "site.css").read_text(encoding="utf-8").startswith("/* il2ks-template: ")
    assert (web / "static" / "il2ks" / "vendor" / "lib.js").read_text(encoding="utf-8") == "var lib;\n"
    assert (web / "static" / "il2ks" / "img" / "icon.svg").read_text(encoding="utf-8") == "<svg/>"
    assert {k: e.version for k, e in tv.load_registry(web).items()} == {
        "templates/il2ks/a.html": Version(1),
        "templates/il2ks/b.html": Version(1),
        "static/il2ks/site.css": Version(1),
    }


def test_a_second_run_changes_nothing(web: Path) -> None:
    tv.bump_templates(web)
    before = {p: p.read_bytes() for p in web.rglob("*") if p.is_file()}
    assert tv.bump_templates(web) == []
    assert {p: p.read_bytes() for p in web.rglob("*") if p.is_file()} == before


def test_a_changed_file_is_bumped_and_the_others_are_left_alone(web: Path) -> None:
    tv.bump_templates(web)
    a, b = web / "templates" / "il2ks" / "a.html", web / "templates" / "il2ks" / "b.html"
    b_before = b.read_bytes()
    a.write_text(a.read_text(encoding="utf-8") + "<p>more</p>\n", encoding="utf-8")

    assert [(x.key, x.kind, x.old_version, x.new_version) for x in tv.bump_templates(web, write=False)] == [
        ("templates/il2ks/a.html", "bump", Version(1), Version(1, 1))
    ]
    assert "v1.0" in a.read_text(encoding="utf-8").splitlines()[0]  # a dry run writes nothing

    actions = tv.bump_templates(web)
    assert [(x.key, x.new_version) for x in actions] == [("templates/il2ks/a.html", Version(1, 1))]
    assert a.read_text(encoding="utf-8").splitlines()[0].startswith("{# il2ks-template: templates/il2ks/a.html v1.1 ")
    assert b.read_bytes() == b_before
    assert tv.load_registry(web)["templates/il2ks/a.html"].version == Version(1, 1)
    assert tv.load_registry(web)["templates/il2ks/b.html"].version == Version(1)
    assert tv.bump_templates(web) == []  # and a third change bumps again, once
    a.write_text(a.read_text(encoding="utf-8") + "x\n", encoding="utf-8")
    assert [x.new_version for x in tv.bump_templates(web)] == [Version(1, 2)]


def test_a_version_raised_by_hand_is_not_bumped_twice(web: Path) -> None:
    tv.bump_templates(web)
    a = web / "templates" / "il2ks" / "a.html"
    text = tv.with_header(a.read_text(encoding="utf-8") + "<p>more</p>\n", "templates/il2ks/a.html", Version(5))
    a.write_text(text, encoding="utf-8")
    tv.bump_templates(web)
    assert tv.version_of(a) == Version(5)
    assert tv.load_registry(web)["templates/il2ks/a.html"].version == Version(5)
    assert tv.bump_templates(web) == []


def test_a_lost_header_is_restored_at_the_registered_version(web: Path) -> None:
    tv.bump_templates(web)
    a = web / "templates" / "il2ks" / "a.html"
    a.write_text(a.read_text(encoding="utf-8") + "<p>2</p>\n", encoding="utf-8")
    tv.bump_templates(web)  # v1.1
    a.write_text("<p>a</p>\n<p>2</p>\n", encoding="utf-8")  # header deleted, content as registered
    assert [x.kind for x in tv.bump_templates(web)] == ["add-header"]
    assert tv.version_of(a) == Version(1, 1)


def test_new_and_removed_files_update_the_registry(web: Path) -> None:
    tv.bump_templates(web)
    (web / "templates" / "il2ks" / "b.html").unlink()
    (web / "templates" / "il2ks" / "c.html").write_text("<p>c</p>", encoding="utf-8")
    kinds = {a.key: a.kind for a in tv.bump_templates(web)}
    assert kinds == {"templates/il2ks/b.html": "drop", "templates/il2ks/c.html": "add-header"}
    assert set(tv.load_registry(web)) == {"templates/il2ks/a.html", "templates/il2ks/c.html", "static/il2ks/site.css"}


def test_crlf_files_keep_their_line_endings(web: Path) -> None:
    a = web / "templates" / "il2ks" / "a.html"
    a.write_bytes(b"<p>a</p>\r\n<p>more</p>\r\n")
    tv.bump_templates(web)
    data = a.read_bytes()
    assert data.count(b"\r\n") == 3
    assert data.count(b"\n") == 3
    assert tv.bump_templates(web) == []


# --- the dev commands -------------------------------------------------------------------------------------------------


def test_cli_bump_templates_check_and_write(web: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert dev_templates.bump_templates(check=True, root=web) == 1
    assert "would change 3 file(s)" in capsys.readouterr().out
    assert not (web / tv.REGISTRY_NAME).exists()
    assert dev_templates.bump_templates(check=False, root=web) == 0
    assert "changed 3 file(s)" in capsys.readouterr().out
    assert dev_templates.bump_templates(check=True, root=web) == 0
    assert "in order" in capsys.readouterr().out


def test_the_real_cli_check_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["dev", "bump-templates", "--check"]) == EXIT_OK
    assert "in order" in capsys.readouterr().out


def test_registry_changes_for_the_release_notes_list_only_n_bumps_and_removals() -> None:
    old = {
        "a": tv.Entry(Version(1), "x"),
        "b": tv.Entry(Version(2), "y"),
        "minor": tv.Entry(Version(3, 1), "m"),
        "gone": tv.Entry(Version(1), "z"),
    }
    new = {
        "a": tv.Entry(Version(1), "x2"),
        "b": tv.Entry(Version(4), "y2"),
        "minor": tv.Entry(Version(3, 4), "m2"),
        "fresh": tv.Entry(Version(1), "w"),
    }
    assert tv.registry_changes(old, new) == ["b: v2.0 -> v4.0", "gone: removed"]
    assert tv.registry_changes(old, new, everything=True) == [
        "b: v2.0 -> v4.0",
        "fresh: new (v1.0)",
        "gone: removed",
        "minor: v3.1 -> v3.4 (minor)",
    ]
    assert tv.registry_changes(new, new) == []


def test_parse_registry_reads_the_saved_format(web: Path) -> None:
    tv.bump_templates(web)
    text = tv.registry_path(web).read_text(encoding="utf-8")
    assert tv.parse_registry(text) == tv.load_registry(web)
    assert tv.parse_registry('{"format": 1}') == {}


def test_a_0_1_0_registry_with_single_numbers_is_read_as_n_dot_0() -> None:
    text = '{"format": 1, "files": {"templates/x.html": {"version": 3, "sha256": "abc"}}}'
    assert tv.parse_registry(text) == {"templates/x.html": tv.Entry(Version(3, 0), "abc", None)}


def test_template_changes_compares_with_the_registry_at_a_tag(
    web: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tv.bump_templates(web)
    old_text = tv.registry_path(web).read_text(encoding="utf-8")
    a = web / "templates" / "il2ks" / "a.html"
    a.write_text(a.read_text(encoding="utf-8") + "more\n", encoding="utf-8")
    tv.bump_templates(web)
    asked: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        asked.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=old_text, stderr="")

    monkeypatch.setattr(dev_templates.subprocess, "run", fake_run)
    assert dev_templates.template_changes("v0.1.0", root=web) == 0
    assert asked == [["git", "show", "v0.1.0:src/il2ks/web/template_versions.json"]]
    out = capsys.readouterr().out
    assert "a.html" not in out  # a minor bump is not listed
    assert "no template or stylesheet changed its override contract" in out
    assert dev_templates.template_changes("v0.1.0", root=web, everything=True) == 0
    assert "- templates/il2ks/a.html: v1.0 -> v1.1 (minor)" in capsys.readouterr().out


def test_template_changes_explains_a_tag_without_a_registry(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 128, stdout="", stderr="fatal: path does not exist")

    monkeypatch.setattr(dev_templates.subprocess, "run", fake_run)
    assert dev_templates.template_changes("v0.0.1") == 2
    assert "Releases before template versions existed" in capsys.readouterr().err
