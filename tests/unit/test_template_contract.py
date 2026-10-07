"""Template versions that tell breaking from cosmetic (TD-25): which edits raise N and which only M.

The contract rules are in `il2ks.serving.templatecontract`; `bump-templates` applies them (`templateversions`)."""

from pathlib import Path

import pytest

from il2ks.serving import templatecontract as tc
from il2ks.serving import templateversions as tv
from il2ks.serving.templateversions import Version

# --- reading a template -----------------------------------------------------------------------------------------------


def contract(text: str, rel: str = "a.html") -> tc.Contract:
    return tc.fingerprint(rel, text)


def test_blocks_includes_and_the_parent_are_read() -> None:
    found = contract(
        '{% extends "il2ks/base.html" %}{% block content %}{% include "il2ks/x.html" with a=b %}{% endblock %}'
        "{% block head %}{% endblock %}"
    )
    assert found["extends"] == ("il2ks/base.html",)
    assert found["blocks"] == ("content", "head")
    assert found["includes"] == ("il2ks/x.html",)


def test_variables_are_root_names_without_what_the_template_binds_itself() -> None:
    found = contract(
        "{% for row in rows %}{{ row.name }}{{ forloop.counter }}{% endfor %}"
        "{% with total=sum|add:extra %}{{ total }}{% endwith %}"
        "{% if user.is_staff and not hidden %}{{ page_obj.number }}{% endif %}"
        "{% blocktranslate with n=qty asvar msg %}{{ n }} items{% endblocktranslate %}"
        '{{ title|default:"x" }}{{ block.super }}'
    )
    assert found["variables"] == ("extra", "hidden", "page_obj", "qty", "rows", "sum", "title", "user")


def test_comments_are_not_code() -> None:
    found = contract(
        "{# {% block old %}{{ gone }} #}{% comment %}{% block doc %}{{ docvar }}{% endcomment %}"
        "{% block real %}{% endblock %}"
    )
    assert found["blocks"] == ("real",)
    assert "variables" not in found


def test_only_il2ks_tags_and_filters_are_recorded_not_djangos_own() -> None:
    found = contract(
        "{% load i18n il2ks %}{% translate 'x' %}{% url 'home' %}"
        "{{ n|floatformat:1|object_name }}{% tour_select tours %}"
    )
    assert found["tags"] == ("tour_select",)
    assert found["filters"] == ("object_name",)


def test_markers_are_the_fixed_ids_classes_and_data_attributes() -> None:
    found = contract(
        '<div id="main" class="card {% if x %}active{% endif %} item-{{ n }}" data-sort="1"><p class=\'a b\' data-x>'
        "</p></div>"
    )
    assert found["markers"] == ("#main", ".a", ".active", ".b", ".card", "[data-sort]", "[data-x]")


def test_css_defines_classes_ids_and_custom_properties_not_values() -> None:
    found = contract(
        ":root { --il2-bg: #fff; } .card:hover, #main > .card__title { color: #abc; margin: 0.5rem; }\n"
        "@media (min-width: 40rem) { .wide[data-mode] { width: 1.5em; } } /* .commented { } */",
        "site.css",
    )
    assert found["defines"] == ("#main", "--il2-bg", ".card", ".card__title", ".wide", "[data-mode]")


def test_js_selectors_are_what_the_script_looks_up() -> None:
    found = contract(
        "const a = document.getElementById('results');\n"
        "const b = el.closest('.table-wrap');\n"
        "document.querySelectorAll('[data-hint-open], .col-tip').forEach(f);\n"
        "node.classList.toggle('is-open'); node.dataset.themeToggle = 'x';\n"
        "// document.getElementById('commented')\n",
        "il2ks.js",
    )
    assert found["selectors"] == (
        "#results",
        ".col-tip",
        ".is-open",
        ".table-wrap",
        "[data-hint-open]",
        "[data-theme-toggle]",
    )


# --- breaking or not --------------------------------------------------------------------------------------------------

SITE_CSS = ".card { color: red; } #main { margin: 0; }\n"
BASE = (
    '{% block content %}<div id="main" class="card"><p class="plain">{{ title }}</p></div>{% endblock %}'
    '{% block footer %}{% include "il2ks/f.html" %}{% endblock %}\n'
)


@pytest.fixture
def web(tmp_path: Path) -> Path:
    root = tmp_path / "web"
    (root / "templates" / "il2ks").mkdir(parents=True)
    (root / "static" / "il2ks").mkdir(parents=True)
    (root / "templates" / "il2ks" / "base.html").write_text(BASE, encoding="utf-8")
    (root / "static" / "il2ks" / "site.css").write_text(SITE_CSS, encoding="utf-8")
    (root / "static" / "il2ks" / "app.js").write_text("document.getElementById('results');\n", encoding="utf-8")
    tv.bump_templates(root)
    return root


def edit(web: Path, rel: str, old: str, new: str) -> None:
    path = web / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new), encoding="utf-8")


BASE_KEY = "templates/il2ks/base.html"
BASE_FILE = "templates/il2ks/base.html"


def bumped(web: Path, key: str = BASE_KEY) -> tv.Action:
    (action,) = (a for a in tv.bump_templates(web) if a.key == key)
    assert action.kind == "bump"
    return action


def test_a_fresh_registry_stores_a_fingerprint_for_every_file(web: Path) -> None:
    registry = tv.load_registry(web)
    assert all(entry.contract is not None for entry in registry.values())
    assert tv.bump_templates(web) == []


def test_changed_text_is_a_minor_bump(web: Path) -> None:
    edit(web, BASE_FILE, '<p class="plain">', '<p class="plain"><em>hi</em> ')
    action = bumped(web)
    assert (action.part, action.new_version, action.reasons) == ("minor", Version(1, 1), ())
    assert tv.version_of(web / BASE_FILE) == Version(1, 1)


def test_a_renamed_block_is_a_major_bump_and_resets_the_minor(web: Path) -> None:
    edit(web, BASE_FILE, "<p", "<p data-x")
    bumped(web)  # v1.1
    edit(web, BASE_FILE, "{% block footer %}", "{% block page_footer %}")
    action = bumped(web)
    assert (action.part, action.new_version) == ("major", Version(2, 0))
    assert action.reasons == ("block added: page_footer", "block removed: footer")


def test_an_added_variable_or_include_is_minor_an_override_without_it_still_renders(web: Path) -> None:
    edit(web, BASE_FILE, "{{ title }}", "{{ title }} {{ subtitle }}")
    action = bumped(web)
    assert (action.part, action.reasons) == ("minor", ())
    edit(web, BASE_FILE, "{{ title }}", "{{ title }}{% include 'il2ks/more.html' %}{{ more }}")
    assert bumped(web).part == "minor"


def test_a_removed_variable_and_a_changed_include_are_major(web: Path) -> None:
    edit(web, BASE_FILE, "{{ title }}", "")
    assert bumped(web).reasons == ("variable removed: title",)
    edit(web, BASE_FILE, "il2ks/f.html", "il2ks/g.html")
    assert bumped(web).reasons == ("include removed: il2ks/f.html",)  # the new one is only missing in an override


def test_a_removed_il2ks_tag_or_filter_is_major_an_added_one_is_not(web: Path) -> None:
    edit(web, BASE_FILE, "{{ title }}", "{{ title|object_name }}{% tour_select tours %}")
    action = bumped(web)
    assert action.part == "minor"  # `tours` is a new variable: an override without it still renders
    edit(web, BASE_FILE, "{% tour_select tours %}", "")
    assert bumped(web).reasons == ("variable removed: tours", "tag removed: tour_select")
    edit(web, BASE_FILE, "|object_name", "")
    assert bumped(web).reasons == ("filter removed: object_name",)


def test_an_id_the_css_targets_removed_from_the_template_is_major(web: Path) -> None:
    edit(web, BASE_FILE, ' id="main"', "")
    action = bumped(web)
    assert (action.part, action.reasons) == ("major", ("id/class used by CSS or script removed: #main",))


def test_a_class_that_no_stylesheet_or_script_targets_is_not_part_of_the_contract(web: Path) -> None:
    edit(web, BASE_FILE, 'class="plain"', 'class="plain fancy"')
    assert bumped(web).part == "minor"  # `.fancy` is in no stylesheet
    edit(web, BASE_FILE, 'class="card"', 'class="card plain"')
    assert bumped(web).part == "minor"  # `.plain` was there already


def test_removing_a_targeted_class_is_major(web: Path) -> None:
    edit(web, BASE_FILE, 'class="card"', 'class="boxed"')
    action = bumped(web)
    assert action.part == "major"
    assert "id/class used by CSS or script removed: .card" in action.reasons


def test_a_script_target_counts_like_a_css_target(web: Path) -> None:
    edit(web, BASE_FILE, "{{ title }}", '{{ title }}<div id="results"></div>')
    assert bumped(web).reasons == ("id/class used by CSS or script added: #results",)


def test_css_defining_a_new_class_is_major_changing_a_value_is_minor(web: Path) -> None:
    css = "static/il2ks/site.css"
    edit(web, css, "color: red", "color: blue")
    assert bumped(web, "static/il2ks/site.css").part == "minor"
    edit(web, css, "#main", "#main, .new-thing")
    action = bumped(web, "static/il2ks/site.css")
    assert (action.part, action.reasons) == ("major", ("style added: .new-thing",))


def test_js_looking_up_a_new_element_is_major(web: Path) -> None:
    js = "static/il2ks/app.js"
    edit(web, js, "results", "results2")
    assert bumped(web, "static/il2ks/app.js").part == "major"


def test_major_forces_n_for_the_named_changed_files_only(web: Path) -> None:
    edit(web, BASE_FILE, "<p", "<p lang='en'")
    edit(web, "static/il2ks/site.css", "color: red", "color: blue")
    actions = {a.key: a for a in tv.bump_templates(web, major=["il2ks/base.html"])}
    assert actions[BASE_KEY].part == "major"
    assert actions[BASE_KEY].new_version == Version(2, 0)
    assert actions["static/il2ks/site.css"].part == "minor"


def test_major_without_names_forces_every_changed_file(web: Path) -> None:
    edit(web, BASE_FILE, "<p", "<p lang='en'")
    actions = tv.bump_templates(web, major=[])
    assert [(a.key, a.part) for a in actions] == [(BASE_KEY, "major")]


def test_a_dry_run_reports_the_part_and_writes_nothing(web: Path) -> None:
    edit(web, BASE_FILE, "{{ title }}", "")
    before = (web / BASE_FILE).read_bytes()
    (action,) = tv.bump_templates(web, write=False)
    assert action.part == "major"
    assert (web / BASE_FILE).read_bytes() == before
    assert "BREAKING" in tv.describe(action)
    assert "variable removed: title" in tv.describe(action)


# --- the 0.1.0 compatibility ------------------------------------------------------------------------------------------


def test_old_single_number_headers_read_as_n_dot_0() -> None:
    found = tv.find_header(
        "{# il2ks-template: templates/a.html v7 - copy this line along when you override #}", "a.html"
    )
    assert found is not None
    assert found.version == Version(7, 0)
    assert found.minor_given is False
    new = tv.find_header("{# il2ks-template: templates/a.html v7.3 - x #}", "a.html")
    assert new is not None
    assert new.version == Version(7, 3)
    assert new.minor_given is True
    css = tv.find_header("/* il2ks-template: static/a.css v2 - x */", "a.css")
    assert css is not None
    assert css.version == Version(2, 0)


@pytest.mark.parametrize("text", ["v3", "3", "3.1", "v3.1", " 3.0 "])
def test_parse_version_accepts_old_and_new_spellings(text: str) -> None:
    assert tv.parse_version(text) == Version(int(text.strip("v ").partition(".")[0]), int(text.partition(".")[2] or 0))


@pytest.mark.parametrize("text", ["", "v", "3.x", "three", "3.1.2"])
def test_parse_version_rejects_nonsense(text: str) -> None:
    assert tv.parse_version(text) is None


def test_versions_order_by_n_then_m() -> None:
    assert Version(2, 0) > Version(1, 9)
    assert Version(1, 2) > Version(1, 1)
    assert Version(3) == Version(3, 0)
    assert str(Version(3, 1)) == "3.1"


def test_a_0_1_0_registry_gets_its_headers_and_fingerprints_without_a_bump(web: Path) -> None:
    """The first run of this tool over a tree that has single-number headers and a format-1 registry changes no
    version number: N stays as it was, M is 0, and the fingerprints are recorded."""
    base = web / BASE_FILE
    base.write_text(
        tv.with_header(base.read_text(encoding="utf-8"), BASE_KEY, Version(4)).replace("v4.0", "v4"), encoding="utf-8"
    )
    registry = tv.load_registry(web)
    old = {k: tv.Entry(Version(4) if k == BASE_KEY else e.version, e.sha256, None) for k, e in registry.items()}
    tv.save_registry(old, web)
    text = tv.registry_path(web).read_text(encoding="utf-8").replace('"format": 2', '"format": 1')
    tv.registry_path(web).write_text(text, encoding="utf-8")

    actions = tv.bump_templates(web)
    assert {a.key for a in actions} >= {BASE_KEY}
    assert all(a.kind != "bump" for a in actions)
    assert tv.version_of(base) == Version(4, 0)
    assert base.read_text(encoding="utf-8").startswith("{# il2ks-template: templates/il2ks/base.html v4.0 ")
    assert tv.load_registry(web)[BASE_KEY].version == Version(4, 0)
    assert tv.load_registry(web)[BASE_KEY].contract is not None
    assert tv.bump_templates(web) == []


def test_a_branch_that_bumped_with_the_old_tool_is_judged_again(web: Path) -> None:
    """A branch made before this change bumped `v1` -> `v2` for a wording edit. Re-running the tool at merge time keeps
    the registry's N, because the contract did not change."""
    edit(web, BASE_FILE, "<p", "<p lang='en'")
    path = web / BASE_FILE
    path.write_text(
        tv.with_header(path.read_text(encoding="utf-8"), BASE_KEY, Version(2)).replace("v2.0", "v2"), encoding="utf-8"
    )
    (action,) = tv.bump_templates(web)
    assert (action.kind, action.part, action.new_version) == ("bump", "minor", Version(1, 1))


def test_a_version_raised_by_hand_with_a_minor_number_is_believed(web: Path) -> None:
    edit(web, BASE_FILE, "<p", "<p lang='en'")
    path = web / BASE_FILE
    path.write_text(tv.with_header(path.read_text(encoding="utf-8"), BASE_KEY, Version(3, 0)), encoding="utf-8")
    (action,) = tv.bump_templates(web)
    assert (action.kind, action.new_version) == ("register", Version(3, 0))


def test_the_real_templates_have_fingerprints_and_a_stable_registry() -> None:
    registry = tv.load_registry()
    assert registry["templates/il2ks/base.html"].contract is not None
    assert "content" in dict(registry["templates/il2ks/base.html"].contract or {}).get("blocks", ())
    assert "#main" in dict(registry["templates/il2ks/base.html"].contract or {}).get("markers", ())


def test_url_names_and_static_paths_are_in_the_fingerprint() -> None:
    found = contract(
        '{% load static %}<a href="{% url \'web:player\' player.pk %}">{% url "home" as h %}{% url dyn %}</a>'
        '<link href="{% static \'il2ks/site.css\' %}"><img src="{% static "il2ks/a.svg" %}">'
    )
    assert found["urls"] == ("home", "web:player")
    assert found["static"] == ("il2ks/a.svg", "il2ks/site.css")


def test_a_removed_or_renamed_url_name_is_major_an_added_one_is_minor(web: Path) -> None:
    edit(web, BASE_FILE, "<p", "<a href=\"{% url 'web:home' %}\"></a><p")
    assert bumped(web).part == "minor"
    edit(web, BASE_FILE, "<p", "<a href=\"{% url 'web:about' %}\"></a><p")
    assert bumped(web).part == "minor"
    edit(web, BASE_FILE, "'web:home'", "'web:start'")
    action = bumped(web)
    assert (action.part, action.reasons) == ("major", ("url name removed: web:home",))


def test_url_calls_are_recorded_with_their_argument_count() -> None:
    found = contract(
        "{% url 'home' %}{% url 'web:player' player.pk %}{% url 'web:x' a b as link %}"
        "{% url 'web:k' pk=3 %}{% url dyn 1 %}"
    )
    assert found["url_args"] == ("home/0", "web:k/1", "web:player/1", "web:x/2")


def test_a_changed_argument_count_of_a_url_is_major(web: Path) -> None:
    edit(web, BASE_FILE, "<p", "<a href=\"{% url 'web:player' player.pk %}\"></a><p")
    assert bumped(web).part == "minor"
    edit(web, BASE_FILE, "'web:player' player.pk", "'web:player' player.slug extra")
    action = bumped(web)
    assert (action.part, action.reasons) == ("major", ("url arguments removed: web:player/1",))


def test_a_removed_or_renamed_static_path_is_major_an_added_one_is_minor(web: Path) -> None:
    edit(web, BASE_FILE, "<p", "<img src=\"{% static 'il2ks/a.svg' %}\"><p")
    assert bumped(web).part == "minor"
    edit(web, BASE_FILE, "il2ks/a.svg", "il2ks/b.svg")
    action = bumped(web)
    assert (action.part, action.reasons) == ("major", ("static file removed: il2ks/a.svg",))


def test_a_marker_removed_from_the_template_and_the_css_in_one_change_is_still_major(web: Path) -> None:
    edit(web, BASE_FILE, ' id="main"', "")
    edit(web, "static/il2ks/site.css", " #main { margin: 0; }", "")
    actions = {a.key: a for a in tv.bump_templates(web)}
    assert actions[BASE_KEY].reasons == ("id/class used by CSS or script removed: #main",)
    assert actions["static/il2ks/site.css"].reasons == ("style removed: #main",)
