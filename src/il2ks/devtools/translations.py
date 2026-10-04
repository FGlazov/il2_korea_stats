"""Translation workflow (TD-24, NFR-I18N-1): extract, merge, compile and check the `.po` / `.mo` files.

Pure Python on purpose: Django's own `makemessages` / `compilemessages` need GNU gettext, which a Windows machine or a
minimal CI image usually lacks. Here Babel does the catalog work and Django's own `templatize` turns a template into
the pseudo-Python the Babel Python extractor reads (the same trick `makemessages` plays with `xgettext`), so the
extracted strings are exactly the ones Django will look up at run time.

Layout: `src/il2ks/locale/<ll>/LC_MESSAGES/django.po` (+ the compiled `django.mo`, committed so the wheel has it) for
every language except English, whose msgids are the source text.

Draft marking: a machine-translated entry carries the translator comment `# llm-draft` and stays **active** (a `fuzzy`
flag would hide it from the compiled catalog). A reviewer deletes that line when they have checked the translation, so
`status` shows exactly how much review is left per language.

Commands (`il2ks dev translations ...`): `update` (extract + merge + compile), `compile`, `status`, `import`.
"""

import io
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from babel.messages.catalog import Catalog, Message
from babel.messages.extract import DEFAULT_KEYWORDS, extract
from babel.messages.mofile import read_mo, write_mo
from babel.messages.pofile import read_po, write_po
from django.utils.translation.template import templatize

PACKAGE = Path(__file__).resolve().parents[1]
LOCALE_DIR = PACKAGE / "locale"
DRAFT_MARK = "llm-draft"
"""The translator comment that marks an entry as an unreviewed machine translation."""

# (Django language code, gettext locale directory, GNU Plural-Forms header)
type LanguageSpec = tuple[str, str, str]
TARGET_LANGUAGES: tuple[LanguageSpec, ...] = (
    (
        "ru",
        "ru",
        "nplurals=3; plural=(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2);",
    ),
    ("de", "de", "nplurals=2; plural=(n != 1);"),
    ("es", "es", "nplurals=2; plural=(n != 1);"),
    ("fr", "fr", "nplurals=2; plural=(n > 1);"),
    ("pt-br", "pt_BR", "nplurals=2; plural=(n > 1);"),
)
"""The languages of TD-24. Keep in step with `serving.djsettings.LANGUAGES`; a test checks it."""

KEYWORDS: dict[str, object] = {
    **DEFAULT_KEYWORDS,
    "gettext_lazy": None,
    "gettext_noop": None,
    "ngettext_lazy": (1, 2),
    "pgettext_lazy": ((1, "c"), 2),
    "npgettext_lazy": ((1, "c"), 2, 3),
}
SKIPPED_TEMPLATES = frozenset({"styleguide.html"})  # English on purpose: a DEBUG-only developer page
_PLACEHOLDER = re.compile(r"%\(\w+\)[sdif]|%[sdif]|%%|\{\w*\}")


class TranslationError(Exception):
    """A problem the developer has to fix (bad language code, placeholder mismatch, ...)."""


def _source_files() -> list[Path]:
    files = [p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts and "migrations" not in p.parts]
    files += [p for p in PACKAGE.rglob("*.html") if p.name not in SKIPPED_TEMPLATES]
    return sorted(files)


def extract_catalog() -> Catalog:
    """Every translatable string of the package (Python and templates) as one catalog, without locations (they would
    only make every diff noisy; the msgids are searchable)."""
    template = Catalog(project="il2ks", version="", charset="utf-8")
    for path in _source_files():
        source = path.read_text(encoding="utf-8")
        if path.suffix == ".html":
            # templatize blanks out the HTML with X's but keeps its indentation, which Python's strict tokenizer rejects
            # (xgettext, which `makemessages` uses, is lenient). Strings never span lines, so stripping is safe.
            lines = templatize(source, origin=str(path)).splitlines()
            source = "\n".join(line.lstrip() for line in lines)
        found = extract("python", io.BytesIO(source.encode("utf-8")), KEYWORDS, ["Translators:"], {})  # pyright: ignore[reportArgumentType]
        for _line, message, comments, context in found:
            template.add(message, context=context, auto_comments=comments)
    return template


def po_path(directory: str) -> Path:
    return LOCALE_DIR / directory / "LC_MESSAGES" / "django.po"


def mo_path(directory: str) -> Path:
    return po_path(directory).with_suffix(".mo")


def read_catalog(directory: str) -> Catalog:
    with po_path(directory).open("rb") as handle:
        return read_po(handle, locale=directory)


def _po_bytes(catalog: Catalog) -> bytes:
    buffer = io.BytesIO()
    write_po(buffer, catalog, width=120, no_location=True, sort_output=True, ignore_obsolete=True)
    # LF everywhere, whatever the platform: .po files are diffed and reviewed.
    return buffer.getvalue().replace(b"\r\n", b"\n")


def _write_po(catalog: Catalog, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_po_bytes(catalog))


def _set_headers(catalog: Catalog, directory: str, plural_forms: str) -> None:
    """Stable, meaningful header lines (Babel's own are placeholders or change on every run). Babel's `plural_forms` is
    read-only; the `mime_headers` setter is the public way in."""
    fixed = {
        "Project-Id-Version": "il2ks",
        "Report-Msgid-Bugs-To": "https://github.com/FGlazov/il2_korea_stats/issues",
        "Last-Translator": "",
        "Language-Team": directory,
        "Plural-Forms": plural_forms,
    }
    headers = [(key, value) for key, value in catalog.mime_headers if key not in fixed]
    catalog.mime_headers = [*headers, *fixed.items()]


def _new_catalog(directory: str) -> Catalog:
    return Catalog(
        locale=directory,
        project="il2ks",
        version="",
        charset="utf-8",
        fuzzy=False,
        header_comment=(
            "# il2ks translations. Machine-drafted entries carry the comment `llm-draft`; delete that line once a\n"
            "# human has reviewed the translation (docs/translating.md)."
        ),
    )


def update(languages: Iterable[LanguageSpec] = TARGET_LANGUAGES) -> dict[str, int]:
    """Re-extract the strings and merge them into every language's `.po`: new msgids are added empty, removed ones are
    dropped, existing translations and their `llm-draft` marks are kept. Returns the number of messages per language."""
    template = extract_catalog()
    counts: dict[str, int] = {}
    for _code, directory, plural_forms in languages:
        catalog = _merged(template, directory, plural_forms)
        _write_po(catalog, po_path(directory))
        counts[directory] = len(catalog)
    return counts


def _merged(template: Catalog, directory: str, plural_forms: str) -> Catalog:
    path = po_path(directory)
    catalog = read_catalog(directory) if path.exists() else _new_catalog(directory)
    _set_headers(catalog, directory, plural_forms)
    catalog.update(template, no_fuzzy_matching=True, update_header_comment=False, update_creation_date=False)
    return catalog


def check(languages: Iterable[LanguageSpec] = TARGET_LANGUAGES) -> list[str]:
    """What `update` + `compile_all` would change in the shipped files, as problem lines (empty = up to date). Writes
    nothing. For `il2ks dev translations check` (agents, pre-commit, CI)."""
    template = extract_catalog()
    problems: list[str] = []
    for _code, directory, plural_forms in languages:
        po = po_path(directory)
        if not po.is_file():
            problems.append(f"{po} is missing")
            continue
        on_disk = po.read_bytes().replace(b"\r\n", b"\n")  # a Windows checkout may have CRLF; git stores LF
        if _po_bytes(_merged(template, directory, plural_forms)) != on_disk:
            problems.append(f"{directory}: django.po is out of date with the templates and sources")
        mo = mo_path(directory)
        if not mo.is_file():
            problems.append(f"{directory}: django.mo is missing")
            continue
        compiled = read_mo(io.BytesIO(mo.read_bytes()))
        for message in messages_of(read_catalog(directory)):
            if is_translated(message):
                found = compiled.get(message.id, message.context)
                if found is None or _strings(found)[1] != _strings(message)[1]:
                    problems.append(f"{directory}: django.mo is stale (compiled from an older django.po)")
                    break
    return problems


def compile_all(languages: Iterable[LanguageSpec] = TARGET_LANGUAGES) -> list[Path]:
    """Compile every `.po` into its `.mo` (untranslated and fuzzy entries are left out, so English shows through)."""
    written: list[Path] = []
    for _code, directory, _plural in languages:
        if not po_path(directory).exists():
            continue
        buffer = io.BytesIO()
        write_mo(buffer, read_catalog(directory), use_fuzzy=False)
        target = mo_path(directory)
        target.write_bytes(buffer.getvalue())
        written.append(target)
    return written


def placeholders(text: str) -> Counter[str]:
    """The format placeholders of a string (`%(name)s`, `%s`, `%%`, `{}`, `{name}`), counted."""
    return Counter(_PLACEHOLDER.findall(text))


def _strings(message: Message) -> tuple[list[str], list[str]]:
    """(msgids, msgstrs) as lists, so singular and plural messages are handled alike."""
    ids = [message.id] if isinstance(message.id, str) else list(message.id)
    raw = message.string
    strings = [raw or ""] if raw is None or isinstance(raw, str) else list(raw)
    return ids, strings


def placeholder_problems(message: Message) -> list[str]:
    """Where a translation's placeholders differ from the English text. Plain messages must match exactly. In a plural
    message a form may leave out the number (`one file` for 1), but never invents a placeholder."""
    ids, strings = _strings(message)
    plural = len(ids) > 1
    problems: list[str] = []
    for index, text in enumerate(strings):
        if not text:
            continue
        reference = ids[0] if index == 0 else ids[-1]
        wanted, got = placeholders(reference), placeholders(text)
        if (got - wanted) if plural else (wanted != got):
            problems.append(f"{reference!r} -> {text!r}: placeholders {dict(got)} vs {dict(wanted)}")
    return problems


def is_translated(message: Message) -> bool:
    _ids, strings = _strings(message)
    return all(strings)


def is_draft(message: Message) -> bool:
    return DRAFT_MARK in (comment.strip() for comment in message.user_comments)


@dataclass(frozen=True, slots=True)
class LanguageStatus:
    language: str
    total: int
    translated: int
    drafts: int
    untranslated: int

    @property
    def reviewed(self) -> int:
        return self.translated - self.drafts


def messages_of(catalog: Catalog) -> list[Message]:
    """The real entries (not the header)."""
    return [message for message in catalog if message.id]


def status(languages: Iterable[LanguageSpec] = TARGET_LANGUAGES) -> list[LanguageStatus]:
    result: list[LanguageStatus] = []
    for code, directory, _plural in languages:
        messages = messages_of(read_catalog(directory))
        translated = [m for m in messages if is_translated(m)]
        result.append(
            LanguageStatus(
                language=code,
                total=len(messages),
                translated=len(translated),
                drafts=sum(1 for m in translated if is_draft(m)),
                untranslated=len(messages) - len(translated),
            )
        )
    return result


def _find(code: str) -> LanguageSpec:
    for spec in TARGET_LANGUAGES:
        if code in (spec[0], spec[1]):
            return spec
    known = ", ".join(spec[0] for spec in TARGET_LANGUAGES)
    raise TranslationError(f"unknown language {code!r} (known: {known})")


def missing(code: str) -> dict[str, str | list[str]]:
    """The untranslated msgids of a language, as the JSON object `import_drafts` reads back (values left empty)."""
    _code, directory, _plural = _find(code)
    result: dict[str, str | list[str]] = {}
    for message in messages_of(read_catalog(directory)):
        if is_translated(message):
            continue
        ids = _strings(message)[0]
        key = ids[0] if message.context is None else f"{message.context}\x04{ids[0]}"
        result[key] = "" if len(ids) == 1 else ["" for _ in ids]
    return result


def import_drafts(code: str, drafts: Mapping[str, str | list[str]]) -> int:
    """Fill the **untranslated** entries of a language from `{msgid: msgstr}` (a list of forms for plural messages) and
    mark each as `llm-draft`. Existing translations are never overwritten. Returns how many entries were filled."""
    _code, directory, plural_forms = _find(code)
    catalog = read_catalog(directory)
    filled = 0
    for message in messages_of(catalog):
        ids = _strings(message)[0]
        key = ids[0] if message.context is None else f"{message.context}\x04{ids[0]}"
        draft = drafts.get(key)
        if draft is None or is_translated(message):
            continue
        if isinstance(draft, str) == (len(ids) > 1) or (isinstance(draft, list) and len(draft) != catalog.num_plurals):
            raise TranslationError(
                f"{key!r}: a plural message needs a list of {catalog.num_plurals} forms, else a string"
            )
        message.string = draft if isinstance(draft, str) else tuple(draft)
        problems = placeholder_problems(message)
        if problems:
            raise TranslationError("; ".join(problems))
        message.user_comments = [*message.user_comments, DRAFT_MARK]
        filled += 1
    _set_headers(catalog, directory, plural_forms)
    _write_po(catalog, po_path(directory))
    return filled


def load_drafts(path: Path) -> dict[str, str | list[str]]:
    data: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TranslationError(f"{path}: expected a JSON object {{msgid: msgstr}}")
    result: dict[str, str | list[str]] = {}
    for key, value in cast(dict[object, object], data).items():
        if isinstance(key, str) and isinstance(value, str):
            result[key] = value
        elif isinstance(key, str) and isinstance(value, list):
            forms = [form for form in cast(list[object], value) if isinstance(form, str)]
            if len(forms) != len(cast(list[object], value)):
                raise TranslationError(f"{path}: bad entry {key!r}")
            result[key] = forms
        else:
            raise TranslationError(f"{path}: bad entry {key!r}")
    return result
