"""The admin's Achievements page: the rows it shows and the parsing of its one form (FR-WEB-26, doc 17; doc 16).

Form field names, per achievement `<key>`: `on-<key>` (checkbox), `name-<key>-<lang>` and `desc-<key>-<lang>` (custom
text, blank = the built-in translated one), `thr-<key>-<n>` (threshold of tier n, all blank = the built-in ones), and
`reset` (the "Reset to default" submit buttons, value = the key: that achievement goes back to the built-in switch,
words and thresholds, whatever else was typed in its fields). Everything is plain text (the templates escape it);
nothing is saved when anything is invalid (`parse_form` returns the errors and the typed thresholds, so the form
comes back as typed)."""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from django.conf import settings
from django.http import QueryDict
from django.utils.translation import gettext as _

from il2ks.core.achievement_rules import MAX_THRESHOLD, Rules, threshold_problem
from il2ks.core.achievements import ACHIEVEMENTS
from il2ks.web import medals
from il2ks.web.achievement_config import MAX_DESCRIPTION, MAX_NAME, AchievementConfig, Texts

_SPACES = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class LanguageText:
    code: str
    language: str  # its name
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class AchievementRow:
    key: str
    title: str  # the built-in name in the admin's language
    default_description: str
    kind: str  # "medal", "ribbon" or "shame" (the group it is shown in)
    unit: str
    on: bool
    default_thresholds: tuple[int, ...]
    thresholds: tuple[str, ...]  # as the form shows them (typed text when a save was rejected)
    texts: tuple[LanguageText, ...]
    customised: bool  # anything differs from the built-in
    changes_rows: bool  # its switch or thresholds are not what the stored rows were computed with


def _clean(text: str) -> str:
    return _SPACES.sub(" ", text).strip()


def language_choices() -> list[tuple[str, str]]:
    return [(code, str(name)) for code, name in settings.LANGUAGES]


def build_rows(config: AchievementConfig, typed: Mapping[str, tuple[str, ...]] | None = None) -> list[AchievementRow]:
    rows: list[AchievementRow] = []
    languages = language_choices()
    for a in ACHIEVEMENTS:
        name, description = medals.TEXTS[a.key]
        wanted = config.wanted.thresholds.get(a.key, a.thresholds)
        texts = tuple(
            LanguageText(
                code,
                language,
                config.names.get(a.key, {}).get(code, ""),
                config.descriptions.get(a.key, {}).get(code, ""),
            )
            for code, language in languages
        )
        on = config.wanted.enabled(a.key)
        applied_on = config.applied.enabled(a.key)
        rows.append(
            AchievementRow(
                key=a.key,
                title=str(name),
                default_description=str(description),
                kind="shame" if a.shame else a.kind,
                unit=a.unit,
                on=on,
                default_thresholds=a.thresholds,
                thresholds=(typed or {}).get(a.key) or tuple(str(n) for n in wanted),
                texts=texts,
                customised=(not on or wanted != a.thresholds or any(t.name or t.description for t in texts)),
                changes_rows=on != applied_on or config.applied.thresholds.get(a.key, a.thresholds) != wanted,
            )
        )
    return rows


def _problem_text(problem: str, name: str) -> str:
    match problem:
        case "count":
            return _("%(name)s: give a threshold for every tier, or leave them all blank for the built-in ones.") % {
                "name": name
            }
        case "positive":
            return _("%(name)s: thresholds must be whole numbers of at least 1.") % {"name": name}
        case "increasing":
            return _("%(name)s: each threshold must be larger than the one before.") % {"name": name}
        case _:
            return _("%(name)s: thresholds can be at most %(max)d.") % {"name": name, "max": MAX_THRESHOLD}


def parse_form(
    post: QueryDict, current: AchievementConfig
) -> tuple[AchievementConfig, list[str], dict[str, tuple[str, ...]]]:
    """The configuration the posted form describes, the problems that stop it from being saved, and the thresholds as
    typed (for showing the form again). The applied rules are never touched here."""
    errors: list[str] = []
    typed: dict[str, tuple[str, ...]] = {}
    reset = set(post.getlist("reset"))
    languages = [code for code, _name in language_choices()]
    off: set[str] = set()
    thresholds: dict[str, tuple[int, ...]] = {}
    names: dict[str, dict[str, str]] = {}
    descriptions: dict[str, dict[str, str]] = {}
    for a in ACHIEVEMENTS:
        if a.key in reset:
            continue
        title = str(medals.TEXTS[a.key][0])
        if not post.get(f"on-{a.key}"):
            off.add(a.key)
        for field, store, limit in (("name", names, MAX_NAME), ("desc", descriptions, MAX_DESCRIPTION)):
            for code in languages:
                text = _clean(post.get(f"{field}-{a.key}-{code}", ""))
                if not text:
                    continue
                if len(text) > limit:
                    errors.append(
                        _("%(name)s: a text is longer than %(max)d characters: %(start)s...")
                        % {"name": title, "max": limit, "start": text[:30]}
                    )
                store.setdefault(a.key, {})[code] = text
        raw = tuple(post.get(f"thr-{a.key}-{n}", "").strip() for n in range(1, a.top_tier + 1))
        typed[a.key] = raw
        if not any(raw):
            continue
        try:
            values = tuple(int(v) for v in raw)
        except ValueError:
            errors.append(_problem_text("positive", title))
            continue
        problem = threshold_problem(a, values)
        if problem is not None:
            errors.append(_problem_text(problem, title))
        elif values != a.thresholds:
            thresholds[a.key] = values
    config = AchievementConfig(Rules(frozenset(off), thresholds), current.applied, _texts(names), _texts(descriptions))
    return config, errors, typed if errors else {}


def _texts(raw: dict[str, dict[str, str]]) -> Texts:
    return {key: dict(per_language) for key, per_language in raw.items()}
