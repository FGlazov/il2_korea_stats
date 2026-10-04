"""The translation pipeline and the shipped catalogs (TD-24, NFR-I18N-1).

The pipeline tests work in a temporary locale folder. The "shipped" tests read the real `src/il2ks/locale/` and fail
with the one command that fixes them: `uv run il2ks dev translations update`."""

import gettext
import io
import shutil
import subprocess
from pathlib import Path

import pytest
from babel.messages.catalog import Catalog, Message
from babel.messages.mofile import read_mo

from il2ks.devtools import translations
from il2ks.devtools.translations import TARGET_LANGUAGES, LanguageSpec
from il2ks.serving.djsettings import LANGUAGES

FIX = "run `uv run il2ks dev translations update`"
DIRECTORIES = [directory for _code, directory, _plural in TARGET_LANGUAGES]
CODES = [code for code, _directory, _plural in TARGET_LANGUAGES]
DE: LanguageSpec = ("de", "de", "nplurals=2; plural=(n != 1);")


def _forms(string: str | list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    return (string or "",) if string is None or isinstance(string, str) else tuple(string)


def keys_of(catalog: Catalog) -> set[tuple[str | None, str]]:
    """(context, msgid) of every real entry."""
    return {(m.context, m.id if isinstance(m.id, str) else m.id[0]) for m in translations.messages_of(catalog)}


# --- settings --------------------------------------------------------------------------------------------------------
def test_languages_are_english_plus_the_five_of_td_24() -> None:
    assert [code for code, _name in LANGUAGES] == ["en", "ru", "de", "es", "fr", "pt-br"]
    assert [code for code, _name in LANGUAGES[1:]] == CODES


def test_django_looks_for_translations_inside_the_package() -> None:
    from django.conf import settings

    assert [Path(p) for p in settings.LOCALE_PATHS] == [translations.LOCALE_DIR]
    assert translations.LOCALE_DIR.parent.name == "il2ks"


# --- the shipped catalogs ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("directory", DIRECTORIES)
def test_shipped_po_has_every_string_of_the_sources(directory: str) -> None:
    """Every msgid of the templates and Python code is in every language (untranslated ones may be empty)."""
    wanted = keys_of(translations.extract_catalog())
    have = keys_of(translations.read_catalog(directory))
    assert wanted - have == set(), f"strings missing from {directory}: {FIX}"
    assert have - wanted == set(), f"strings no longer in the sources, still in {directory}: {FIX}"


@pytest.mark.parametrize("directory", DIRECTORIES)
def test_shipped_translations_keep_the_placeholders(directory: str) -> None:
    """`%(name)s`, `%d`, `{}` ... must survive translation or the page crashes (or shows the wrong number)."""
    problems = [
        p
        for m in translations.messages_of(translations.read_catalog(directory))
        for p in translations.placeholder_problems(m)
    ]
    assert problems == []


@pytest.mark.parametrize("directory", DIRECTORIES)
def test_shipped_mo_matches_the_po(directory: str) -> None:
    """The committed `.mo` is what the `.po` compiles to, so the wheel carries the translations people reviewed."""
    mo = translations.mo_path(directory)
    assert mo.is_file(), f"{mo} is missing: {FIX}"
    compiled = read_mo(io.BytesIO(mo.read_bytes()))
    for message in translations.messages_of(translations.read_catalog(directory)):
        if not translations.is_translated(message):
            continue
        found = compiled.get(message.id, message.context)
        assert found is not None, f"{message.id!r} is not in {mo}: {FIX}"
        assert tuple(_forms(found.string)) == tuple(_forms(message.string)), f"{message.id!r} differs in {mo}: {FIX}"


@pytest.mark.parametrize("directory", DIRECTORIES)
def test_every_translated_string_is_a_draft_or_reviewed_never_fuzzy(directory: str) -> None:
    """`fuzzy` would hide an entry from the compiled catalog; drafts are marked with a comment instead."""
    for message in translations.messages_of(translations.read_catalog(directory)):
        assert not message.fuzzy, message.id


def test_the_gitignore_keeps_the_compiled_catalogs() -> None:
    """`*.mo` is ignored generally (Python template); the shipped ones must be tracked or the wheel loses them."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("git not available")
    mo = translations.mo_path("de")
    probe = subprocess.run([git, "check-ignore", "-q", str(mo)], cwd=mo.parent, check=False)
    if probe.returncode == 128:
        pytest.skip("not a git checkout")
    assert probe.returncode == 1, f"{mo} is git-ignored: the wheel would ship without translations"


# --- the pipeline, in a temp folder -----------------------------------------------------------------------------------
@pytest.fixture
def locale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(translations, "LOCALE_DIR", tmp_path)
    return tmp_path


def test_update_creates_a_catalog_with_every_string_empty(locale: Path) -> None:
    translations.update([DE])

    catalog = translations.read_catalog("de")
    assert keys_of(catalog) == keys_of(translations.extract_catalog())
    assert all(not translations.is_translated(m) for m in translations.messages_of(catalog))
    assert (locale / "de" / "LC_MESSAGES" / "django.po").read_text(encoding="utf-8").count("\r") == 0
    assert "nplurals=2" in (locale / "de" / "LC_MESSAGES" / "django.po").read_text(encoding="utf-8")


def test_update_is_idempotent_and_keeps_translations_and_draft_marks(locale: Path) -> None:
    translations.update([DE])
    translations.import_drafts("de", {"Players": "Spieler"})
    before = translations.po_path("de").read_bytes()

    translations.update([DE])

    assert translations.po_path("de").read_bytes() == before
    by_id = {m.id: m for m in translations.messages_of(translations.read_catalog("de"))}
    assert by_id["Players"].string == "Spieler"
    assert translations.is_draft(by_id["Players"])


def test_update_adds_new_strings_and_drops_removed_ones(locale: Path) -> None:
    translations.update([DE])
    catalog = translations.read_catalog("de")
    catalog.add("A string that is gone")
    catalog.delete("Players")
    translations.po_path("de").write_bytes(_po(catalog))

    translations.update([DE])

    ids = {key for _context, key in keys_of(translations.read_catalog("de"))}
    assert "Players" in ids
    assert "A string that is gone" not in ids


def test_import_fills_only_untranslated_entries_and_marks_them(locale: Path) -> None:
    translations.update([DE])
    translations.import_drafts("de", {"Players": "Spieler"})

    filled = translations.import_drafts("de", {"Players": "Etwas anderes", "Missions": "Missionen"})

    by_id = {m.id: m for m in translations.messages_of(translations.read_catalog("de"))}
    assert filled == 1  # "Players" was already translated and is never overwritten
    assert by_id["Players"].string == "Spieler"
    assert by_id["Missions"].string == "Missionen"
    assert translations.is_draft(by_id["Missions"])


def test_a_reviewer_removing_the_draft_comment_counts_as_reviewed(locale: Path) -> None:
    translations.update([DE])
    translations.import_drafts("de", {"Players": "Spieler", "Missions": "Missionen"})
    catalog = translations.read_catalog("de")
    for message in translations.messages_of(catalog):
        if message.id == "Players":
            message.user_comments = []
    translations.po_path("de").write_bytes(_po(catalog))

    (row,) = translations.status([DE])

    assert (row.translated, row.drafts, row.reviewed) == (2, 1, 1)
    assert row.untranslated == row.total - 2


def test_import_rejects_translations_that_break_a_placeholder(locale: Path) -> None:
    translations.update([DE])

    with pytest.raises(translations.TranslationError, match="placeholders"):
        translations.import_drafts("de", {"Powered by il2ks %(version)s": "Betrieben mit il2ks"})
    with pytest.raises(translations.TranslationError, match="placeholders"):
        translations.import_drafts("de", {"Powered by il2ks %(version)s": "il2ks %(versions)s"})
    with pytest.raises(translations.TranslationError, match="unknown language"):
        translations.import_drafts("tlh", {})


def test_import_handles_plural_messages(locale: Path) -> None:
    translations.update([DE])
    plural = "%(n)d row hidden from public pages."

    with pytest.raises(translations.TranslationError, match="plural"):
        translations.import_drafts("de", {plural: "%(n)d Zeile ausgeblendet."})
    translations.import_drafts("de", {plural: ["%(n)d Zeile ausgeblendet.", "%(n)d Zeilen ausgeblendet."]})

    by_id = {m.id: m for m in translations.messages_of(translations.read_catalog("de"))}
    assert by_id[(plural, "%(n)d rows hidden from public pages.")].string == (
        "%(n)d Zeile ausgeblendet.",
        "%(n)d Zeilen ausgeblendet.",
    )


def test_plural_forms_may_leave_out_the_number_but_not_invent_placeholders() -> None:
    message = Message(("%(n)d file", "%(n)d files"), ("eine Datei", "%(n)d Dateien"))
    assert translations.placeholder_problems(message) == []
    message = Message(("%(n)d file", "%(n)d files"), ("%(x)d Datei", "%(n)d Dateien"))
    assert translations.placeholder_problems(message)


def test_compile_writes_a_catalog_python_can_load(locale: Path) -> None:
    translations.update([DE])
    translations.import_drafts("de", {"Players": "Spieler"})

    (written,) = translations.compile_all([DE])

    loaded = gettext.GNUTranslations(io.BytesIO(written.read_bytes()))
    assert loaded.gettext("Players") == "Spieler"
    assert loaded.gettext("Missions") == "Missions"  # untranslated: English shows through


def test_missing_lists_the_untranslated_strings_as_import_input(locale: Path) -> None:
    translations.update([DE])
    translations.import_drafts("de", {"Players": "Spieler"})

    todo = translations.missing("de")

    assert "Players" not in todo
    assert todo["Missions"] == ""
    assert todo["%(n)d row hidden from public pages."] == ["", ""]


def test_templates_and_python_are_both_extracted() -> None:
    ids = {key for _context, key in keys_of(translations.extract_catalog())}
    assert "Skip to content" in ids  # {% translate %} in base.html
    assert "Powered by il2ks %(version)s" in ids  # {% blocktranslate %}
    assert "Attack sorties" in ids  # _("...") inside a {% stat_tile %} tag
    assert "Air superiority" in ids  # gettext_lazy in web/display.py


def _po(catalog: Catalog) -> bytes:
    buffer = io.BytesIO()
    translations.write_po(buffer, catalog, width=120, no_location=True, sort_output=True, ignore_obsolete=True)
    return buffer.getvalue()
