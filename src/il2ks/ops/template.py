"""Turn the shipped example `il2ks.toml` into the admin's config: same text and comments, chosen values switched on.

The template (`data/il2ks.example.toml`) has every setting commented out as `#key = default` after an explanation line
and every table header as `#[table]`. Filling it replaces the `#key = default` line of each chosen key with
`key = value` and uncomments the header of each table that got a value. Nothing else changes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path

type Key = tuple[str, str]
"""`(table, key)`; the table is "" for top-level keys."""

_HEADER_RE = re.compile(r"#\[([A-Za-z_][\w.]*)\]")
_SETTING_RE = re.compile(r"#([A-Za-z_]\w*) = ")


class TemplateError(ValueError):
    """The template has no line for a key that was asked to be set (a bug: the template and the caller disagree)."""


def template_text() -> str:
    return files("il2ks").joinpath("data", "il2ks.example.toml").read_text(encoding="utf-8")


def toml_string(value: str | Path) -> str:
    """A TOML basic string: quotes and backslashes (Windows paths) escaped."""
    return json.dumps(str(value), ensure_ascii=False)


def fill_template(text: str, values: Mapping[Key, str]) -> str:
    """Set `values` (TOML literals such as `toml_string(...)`) in the template text.

    A table the template doesn't have at all is appended at the end (so the admin's config can carry settings of a
    newer or optional part); a key missing from a table the template does have raises `TemplateError`."""
    lines = text.splitlines()
    table = ""
    seen_tables: set[str] = set()
    placed: set[Key] = set()
    header_at: dict[str, int] = {}
    for i, line in enumerate(lines):
        header = _HEADER_RE.fullmatch(line)
        if header is not None:
            table = header.group(1)
            seen_tables.add(table)
            header_at[table] = i
            continue
        setting = _SETTING_RE.match(line)
        if setting is not None and (table, setting.group(1)) in values:
            key = (table, setting.group(1))
            lines[i] = f"{key[1]} = {values[key]}"
            placed.add(key)
    for table_name in {t for t, _ in placed if t}:
        lines[header_at[table_name]] = f"[{table_name}]"

    missing = [key for key in values if key not in placed]
    unknown = [key for key in missing if key[0] not in seen_tables and key[0] != ""]
    broken = [key for key in missing if key not in unknown]
    if broken:
        names = ", ".join(f"[{t}] {k}" if t else k for t, k in broken)
        raise TemplateError(f"the example config has no line for: {names}")
    for table_name in dict.fromkeys(t for t, _ in unknown):
        lines += ["", f"[{table_name}]", *(f"{k} = {values[(t, k)]}" for t, k in unknown if t == table_name)]
    return "\n".join(lines) + "\n"
