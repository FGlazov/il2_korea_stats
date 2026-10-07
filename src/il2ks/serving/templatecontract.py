"""What an override has to agree with: the "contract" of a built-in template, stylesheet or script (TD-25).

A template version is two numbers, `vN.M` (`il2ks.serving.templateversions`). N changes when the *override contract*
changes, M for everything else. This module decides which it is. It reads a file and extracts its **contract
fingerprint**: a few sorted lists of names (see `fingerprint`). `bump-templates` stores the fingerprint of every file in
`template_versions.json` and, when the file changes, compares the stored one with the new one (`breaking_changes`): a
difference means N, none means M. Storing the fingerprint (instead of asking git for the old file) works in a shallow
clone, in a sdist, on a branch that is not committed yet, and after a merge.

Fingerprint of a **template** (`.html`, `.txt`):
- `extends`, `blocks`: the `{% extends %}` / `{% block %}` names. Any change.
- `includes`: the `{% include %}` names. Removal only: an override still including a file that is gone breaks, an
  override without a new include renders fine and only lacks its content.
- `variables`: root names of the context variables the template reads (`player` of `{{ player.name }}`), without names
  the template binds itself (`{% for x in ... %}`, `{% with y=... %}`, `as z`). Removal only: a removed one may no
  longer be supplied by the view, an override that does not use a new one renders fine.
- `urls`, `static`: the names of `{% url 'name' %}` and the paths of `{% static 'path' %}` (string literals only).
  Removal only: an override that still reverses a removed or renamed URL name crashes (NoReverseMatch, a 500), one that
  still points at a removed static file crashes with the manifest storage. A new one is simply missing in the override.
- `url_args`: `name/count` of every `{% url 'name' a b %}` literal (count = arguments before `as`). A call with a
  changed argument count (`name/1` gone while the name is still used) is N: an override with the old arguments
  raises NoReverseMatch.
- `tags`, `filters`: tags and filters that are not Django's own (il2ks's `{% tour_select %}`, `|object_name`). Removal
  only: an override that still uses a removed one breaks, an added one is simply missing in the override.
- `markers`: every `#id`, `.class` and `[data-attribute]` the template writes. They count only when the built-in CSS or
  scripts target them (`targeted_names`); adding or removing such a marker changes the contract.

Fingerprint of a **stylesheet**: `defines` = the classes, ids and data attributes in its selectors, and its custom
properties (`--il2-*`). A copy of the stylesheet lacks the ones a newer built-in template needs. Any change.
Fingerprint of a **script**: `selectors` = the ids, classes and data attributes it looks up in the page. Any change:
the markup it works on is part of the contract.

Honest limits: this is a lexical reading, not Django's parser. A variable the template builds through a custom tag's
`as` is seen as local; a CSS class built by string concatenation in a script is not seen; `{% url %}` with a variable
instead of a literal name is not seen. Not covered at all: a URL pattern in `web/urls.py` whose parameters change
(`<int:pk>` -> `<slug:slug>`) while the templates stay as they are; a changed signature of a custom tag or filter
(arguments), an attribute renamed on a context object (`player.nick` -> `player.name`). `--major` is the escape hatch.
"""

import functools
import re
from pathlib import PurePosixPath

type Contract = dict[str, tuple[str, ...]]

TEMPLATE_SUFFIXES = frozenset({".html", ".txt"})

# `{% comment %}...{% endcomment %}` and `{# ... #}` hold documentation (base.html lists its blocks there): not code.
_COMMENT_BLOCK = re.compile(r"\{%\s*comment\b[^%]*%\}.*?\{%\s*endcomment\s*%\}", re.DOTALL)
_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)
_TOKEN = re.compile(r"\{%\s*(?P<tag>.*?)\s*%\}|\{\{\s*(?P<var>.*?)\s*\}\}", re.DOTALL)
_STRING = re.compile(r"""("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')""")
_WORDS = re.compile(r"""(?:[^\s"']+|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')+""")  # whitespace-split, quotes kept whole
_NAME = re.compile(r"^[A-Za-z_][\w.]*$")
_KEYWORDS = frozenset(
    {
        "and", "or", "not", "in", "is", "as", "only", "with", "count", "context", "reversed", "using", "by",
        "True", "False", "None", "true", "false", "none", "==", "!=", "<", ">", "<=", ">=", "forloop", "block",
        "empty", "plural", "asvar",
    }
)  # fmt: skip
_ALWAYS_LOCAL_ROOTS = frozenset({"forloop", "block"})
_CLASS_ATTR = re.compile(r"""\bclass\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
_ID_ATTR = re.compile(r"""\bid\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
_DATA_ATTR = re.compile(r"""\s(data-[A-Za-z][\w-]*)(?=[\s=>/])""")
_HTML_TAG = re.compile(r"<[A-Za-z][^>]*>")
_DYNAMIC = "\x00"  # stands for a `{{ }}` inside an attribute value: the neighbouring token is not a fixed name


@functools.cache
def _django_builtins() -> tuple[frozenset[str], frozenset[str]]:
    """(tag names, filter names) that Django itself provides: changes to them are not il2ks's contract."""
    from django.template import defaultfilters, defaulttags, loader_tags
    from django.templatetags import i18n, l10n, static

    tags: set[str] = set()
    for library in (defaulttags.register, loader_tags.register, i18n.register, l10n.register, static.register):
        tags.update(library.tags)
    filters: set[str] = set(defaultfilters.register.filters)
    for library in (i18n.register, l10n.register, static.register):
        filters.update(library.filters)
    return frozenset(tags), frozenset(filters)


def _is_end_tag(name: str) -> bool:
    return name.startswith("end")


# --- templates ------------------------------------------------------------------------------------------------------


def _split_filters(expression: str) -> tuple[str, list[tuple[str, str]]]:
    """`a.b|default:c|upper` -> (`a.b`, [(`default`, `c`), (`upper`, ``)]); `|` inside quotes is left alone."""
    parts: list[str] = []
    current: list[str] = []
    quote = ""
    for char in expression:
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
            current.append(char)
        elif char == "|":
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    filters: list[tuple[str, str]] = []
    for part in parts[1:]:
        name, _, argument = part.strip().partition(":")
        if name.strip():
            filters.append((name.strip(), argument.strip()))
    return parts[0].strip(), filters


class _Reader:
    """Collects names while walking the tokens of one template."""

    def __init__(self) -> None:
        self.blocks: set[str] = set()
        self.includes: set[str] = set()
        self.urls: set[str] = set()
        self.url_args: set[str] = set()
        self.statics: set[str] = set()
        self.extends: set[str] = set()
        self.variables: set[str] = set()
        self.locals: set[str] = set()
        self.tags: set[str] = set()
        self.filters: set[str] = set()

    def expression(self, text: str) -> None:
        """A value or condition: variables and filter names inside it."""
        for word in _WORDS.findall(text.replace("(", " ").replace(")", " ")):
            self.word(word)

    def word(self, word: str) -> None:
        if "=" in word and not word.startswith(("=", "!", "<", ">")) and not _STRING.match(word):
            key, _, value = word.partition("=")
            if re.fullmatch(r"[A-Za-z_]\w*", key):
                self.word(value)  # a keyword argument: `key` names a parameter, not a context variable
                return
        value, filters = _split_filters(word)
        for name, argument in filters:
            self.filters.add(name)
            if argument:
                self.word(argument)
        if not value or value[0] in "\"'" or value in _KEYWORDS or value[0].isdigit() or value[0] in "-+.":
            return
        if _NAME.match(value):
            self.variables.add(value.split(".")[0])

    def local(self, name: str) -> None:
        for one in name.split(","):
            if one.strip():
                self.locals.add(one.strip().split(".")[0])

    def tag(self, body: str, libraries: tuple[frozenset[str], frozenset[str]]) -> None:
        words = _WORDS.findall(body)
        if not words:
            return
        name, args = words[0], words[1:]
        if name in {"else", "empty"} or _is_end_tag(name):
            return
        if name == "elif":
            self._arguments(args, binds=False)
            return
        if name == "block":
            if args:
                self.blocks.add(args[0])
            return
        if name in {"extends", "include"}:
            if args:
                target = args[0].strip("\"'")
                (self.extends if name == "extends" else self.includes).add(target)
                if args[0][0] not in "\"'":
                    self.word(args[0])  # a variable names the file: its name is the contract
            self._arguments(args[1:], binds=False)  # `with a=b` hands values to the included file
            return
        if name == "for":
            self._for(" ".join(args))
            return
        if name == "load":
            return
        if name in {"url", "static"} and args and args[0][0] in "\"'":
            (self.urls if name == "url" else self.statics).add(args[0].strip("\"'"))
            if name == "url":
                rest = args[1:]
                if "as" in rest:
                    rest = rest[: rest.index("as")]
                self.url_args.add(f"{args[0].strip(chr(34) + chr(39))}/{len(rest)}")
        if name not in libraries[0]:
            self.tags.add(name)
        self._arguments(args, binds=name in {"with", "blocktranslate", "blocktrans"})

    def _for(self, text: str) -> None:
        before, _, after = text.partition(" in ")
        self.local(before.replace(" ", ""))
        self.expression(after.replace(" reversed", ""))

    def _arguments(self, args: list[str], *, binds: bool) -> None:
        """Arguments of a tag. `binds`: `key=value` makes `key` a name of the template (`with`), else a parameter."""
        skip_next = False
        for index, word in enumerate(args):
            if skip_next:
                skip_next = False
                continue
            if word in {"as", "asvar"} and index + 1 < len(args):
                self.local(args[index + 1])
                skip_next = True
                continue
            if binds and re.match(r"^[A-Za-z_]\w*=", word):
                self.local(word.partition("=")[0])
            self.word(word)


def _template_markers(text: str) -> set[str]:
    stripped = _TOKEN.sub(lambda m: _DYNAMIC if m["var"] is not None else " ", text)
    markers: set[str] = set()
    for found in _CLASS_ATTR.finditer(stripped):
        value = found[1] if found[1] is not None else found[2]
        markers.update(f".{token}" for token in value.split() if _DYNAMIC not in token)
    for found in _ID_ATTR.finditer(stripped):
        value = found[1] if found[1] is not None else found[2]
        markers.update(f"#{token}" for token in value.split() if _DYNAMIC not in token)
    for tag in _HTML_TAG.findall(stripped):
        markers.update(f"[{name}]" for name in _DATA_ATTR.findall(_STRING.sub('""', tag)))
    return markers


def _template_fingerprint(text: str) -> Contract:
    code = _COMMENT.sub("", _COMMENT_BLOCK.sub("", text))
    reader = _Reader()
    libraries = _django_builtins()
    for found in _TOKEN.finditer(code):
        if found["var"] is not None:
            reader.expression(found["var"])
        else:
            reader.tag(found["tag"], libraries)
    variables = reader.variables - reader.locals - _ALWAYS_LOCAL_ROOTS
    return _clean(
        {
            "extends": reader.extends,
            "blocks": reader.blocks,
            "includes": reader.includes,
            "urls": reader.urls,
            "url_args": reader.url_args,
            "static": reader.statics,
            "variables": variables,
            "tags": {t for t in reader.tags if t not in libraries[0]},
            "filters": {f for f in reader.filters if f not in libraries[1]},
            "markers": _template_markers(code),
        }
    )


# --- stylesheets and scripts ----------------------------------------------------------------------------------------

_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_CLASS = re.compile(r"\.(-?[A-Za-z_][\w-]*)")
_CSS_ID = re.compile(r"#(-?[A-Za-z_][\w-]*)")
_CSS_DATA = re.compile(r"\[\s*(data-[\w-]+)")
_CSS_PROPERTY = re.compile(r"(--[\w-]+)\s*:")
_CSS_STRING_OR_URL = re.compile(r"""url\([^)]*\)|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'""")


def selector_names(selectors: str) -> set[str]:
    """`#id`, `.class`, `[data-x]` tokens in a piece of selector text (a CSS prelude or a querySelector argument)."""
    text = _CSS_STRING_OR_URL.sub("", selectors)
    names = {f".{m}" for m in _CSS_CLASS.findall(text)}
    names.update(f"#{m}" for m in _CSS_ID.findall(text))
    names.update(f"[{m}]" for m in _CSS_DATA.findall(selectors))
    return names


def _css_preludes(text: str) -> str:
    """The selectors of a stylesheet, without the declarations (and without at-rule heads like `@media (...)`).
    A prelude is whatever stands in front of a `{`; what stands in front of `;` or `}` is a declaration."""
    code = _CSS_COMMENT.sub("", text)
    preludes: list[str] = []
    buffer: list[str] = []
    for char in code:
        if char == "{":
            head = "".join(buffer).strip()
            if head and not head.startswith("@"):
                preludes.append(head)
            buffer = []
        elif char in ";}":
            buffer = []
        else:
            buffer.append(char)
    return ",".join(preludes)


def _css_fingerprint(text: str) -> Contract:
    code = _CSS_COMMENT.sub("", text)
    defined = selector_names(_css_preludes(text))
    defined.update(_CSS_PROPERTY.findall(code))
    return _clean({"defines": defined})


_JS_SELECTOR_CALL = re.compile(
    r"""\b(?:querySelector(?:All)?|closest|matches)\s*\(\s*(?P<q>["'`])(?P<sel>.*?)(?P=q)""", re.DOTALL
)
_JS_ID_CALL = re.compile(r"""\bgetElementById\s*\(\s*["'`]([^"'`]+)["'`]""")
_JS_CLASS_CALL = re.compile(r"""\bclassList\s*\.\s*(?:add|remove|toggle|contains|replace)\s*\(([^)]*)\)""")
_JS_CLASS_NAME_CALL = re.compile(r"""\bgetElementsByClassName\s*\(\s*["'`]([^"'`]+)["'`]""")
_JS_DATASET = re.compile(r"\bdataset\s*\.\s*([A-Za-z_]\w*)")
_JS_DATASET_INDEX = re.compile(r"""\bdataset\s*\[\s*["']([^"']+)["']\s*\]""")
_JS_DATA_ATTR = re.compile(r"""["'`\[](data-[A-Za-z][\w-]*)""")
_JS_COMMENT = re.compile(r"/\*.*?\*/|(?<![:\"'\\])//[^\n]*", re.DOTALL)
_CAMEL = re.compile(r"(?<=[a-z0-9])([A-Z])")


def _js_fingerprint(text: str) -> Contract:
    code = _JS_COMMENT.sub("", text)
    names: set[str] = set()
    for found in _JS_SELECTOR_CALL.finditer(code):
        names.update(selector_names(found["sel"]))
    names.update(f"#{m}" for m in _JS_ID_CALL.findall(code))
    names.update(f".{m}" for m in _JS_CLASS_NAME_CALL.findall(code))
    for call in _JS_CLASS_CALL.findall(code):
        names.update(f".{m}" for m in re.findall(r"""["'`]([^"'`\s]+)["'`]""", call))
    for key in _JS_DATASET.findall(code) + _JS_DATASET_INDEX.findall(code):
        names.add(f"[data-{_CAMEL.sub(r'-\1', key).lower()}]")
    names.update(f"[{m}]" for m in _JS_DATA_ATTR.findall(code))
    return _clean({"selectors": names})


# --- public ---------------------------------------------------------------------------------------------------------


def _clean(raw: dict[str, set[str]]) -> Contract:
    return {key: tuple(sorted(values)) for key, values in raw.items() if values}


def fingerprint(rel: str, text: str) -> Contract:
    """The contract fingerprint of a file (`rel` or its key says the kind by its extension; `text` has no header)."""
    suffix = PurePosixPath(rel).suffix.lower()
    if suffix == ".css":
        return _css_fingerprint(text)
    if suffix == ".js":
        return _js_fingerprint(text)
    return _template_fingerprint(text)


def targeted_names(*fingerprint_sets: dict[str, Contract]) -> frozenset[str]:
    """`#id` / `.class` / `[data-x]` names that the built-in stylesheets and scripts refer to (the markers of templates
    that matter). Each argument maps file keys (`static/il2ks/site.css`) to fingerprints; pass the new ones and the
    stored (old) ones: a name removed from the template *and* from the CSS/JS in one change is only in the old set."""
    names: set[str] = set()
    for key, contract in (item for fingerprints in fingerprint_sets for item in fingerprints.items()):
        suffix = PurePosixPath(key).suffix.lower()
        if suffix == ".css":
            names.update(n for n in contract.get("defines", ()) if not n.startswith("--"))
        elif suffix == ".js":
            names.update(contract.get("selectors", ()))
    return frozenset(names)


def breaking_changes(key: str, old: Contract, new: Contract, targeted: frozenset[str]) -> list[str]:
    """Why going from fingerprint `old` to `new` changes the override contract (N); empty = it does not (M)."""
    suffix = PurePosixPath(key).suffix.lower()
    reasons: list[str] = []

    def both(field: str, label: str, *, only: frozenset[str] | None = None) -> None:
        before, after = set(old.get(field, ())), set(new.get(field, ()))
        for name in sorted((after - before) if only is None else (after - before) & only):
            reasons.append(f"{label} added: {name}")
        for name in sorted((before - after) if only is None else (before - after) & only):
            reasons.append(f"{label} removed: {name}")

    def removed(field: str, label: str) -> None:
        for name in sorted(set(old.get(field, ())) - set(new.get(field, ()))):
            reasons.append(f"{label} removed: {name}")

    if suffix == ".css":
        both("defines", "style")
    elif suffix == ".js":
        both("selectors", "element looked up")
    else:
        both("extends", "parent template")
        both("blocks", "block")
        removed("includes", "include")
        removed("variables", "variable")
        removed("urls", "url name")
        still_used = set(new.get("urls", ()))
        for name in sorted(set(old.get("url_args", ())) - set(new.get("url_args", ()))):
            if name.rpartition("/")[0] in still_used:  # a removed name is reported above
                reasons.append(f"url arguments removed: {name}")
        removed("static", "static file")
        removed("tags", "tag")
        removed("filters", "filter")
        both("markers", "id/class used by CSS or script", only=targeted)
    return reasons
