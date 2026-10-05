"""The game rules of `il2ks.toml` that the site admin may override (maintainer decision 2026-10-05, FR-ADM-7).

Which settings: the game rules (`[score]`, `[ratings]`, `[killboard]`, `[tours]`, `[rules]`, `[replay]`, the board
minimums and `[marks]`). Machine settings (paths, ports, domain, database, backups, logs, server identity, the
watcher's timers) stay in the file: they need a restart or could lock the admin out. `docs/settings.md` has the table.

How: the admin's values are a flat `{"section.key": value}` object (`SiteSettings.rule_settings`; a key that is absent
uses the file or the built-in default). `overlay` puts them over the rules the file gave and reads the result with
`config.load_rule_set`, the same parser that reads the file, so the file and the admin cannot disagree on what is
valid. Pure Python: the database side (wanted vs applied, who applies what) is `il2ks.ingest.rule_store`.

Each field has an `effect`, which says when a change takes hold:
- `rescore`: it changes stored numbers (points, ratings, assists); `watch` or `rebuild-aggregates` re-scores and
  rebuilds level 2 (pending until then),
- `retour`: it moves missions between tours; `watch` or `rebuild-aggregates --retour` reassigns them and rebuilds,
- `reprocess`: it decides what happened in a flight, so it reads new missions at once and older ones only after
  `il2ks reprocess` (the admin's Reprocess page),
- `display`: only the pages read it (board minimums) or the stat marks (rewritten at once).
"""

import dataclasses
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal, cast

from il2ks.config import ConfigError, RuleSet, load_rule_set_from
from il2ks.core.ratings.score import ADMIN_SCORE_FIELDS, ScoreRules
from il2ks.core.replay.config import ReplayRules

type Effect = Literal["rescore", "retour", "reprocess", "display"]
type Kind = Literal["number", "whole", "positive", "positive_whole", "bool", "text", "date"]
type Page = Literal["scoring", "tours", "rules", "leaderboards"]

TRUE_WORDS: Final = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True, slots=True)
class RuleField:
    """One overridable setting. `key` is the file's name for it, `section.name`."""

    section: str
    name: str
    kind: Kind
    effect: Effect
    page: Page

    @property
    def key(self) -> str:
        return f"{self.section}.{self.name}"


def _build() -> tuple[RuleField, ...]:
    fields: list[RuleField] = []
    for rule in dataclasses.fields(ScoreRules):
        if rule.name not in ADMIN_SCORE_FIELDS:
            fields.append(RuleField("score", rule.name, "number", "rescore", "scoring"))
    fields.append(RuleField("killboard", "assists", "bool", "rescore", "scoring"))
    for name in ("start", "k", "cross_pool_weight"):
        fields.append(RuleField("ratings", name, "number", "rescore", "scoring"))
    fields.append(RuleField("tours", "mode", "text", "retour", "tours"))
    fields.append(RuleField("tours", "start", "date", "retour", "tours"))
    fields.append(RuleField("tours", "timezone", "text", "retour", "tours"))
    fields.append(RuleField("rules", "credit_rams", "bool", "reprocess", "rules"))
    fields.append(RuleField("rules", "ram_window_s", "positive", "reprocess", "rules"))
    fields.append(RuleField("rules", "ram_distance_m", "positive", "reprocess", "rules"))
    for rule in dataclasses.fields(ReplayRules):
        if rule.name == "toggles":
            continue
        kind: Kind = "bool" if rule.name == "resupply_allowed" else "number"
        fields.append(RuleField("replay", rule.name, kind, "reprocess", "rules"))
    minimums: list[tuple[str, Kind]] = [
        ("min_sorties", "whole"),
        ("min_elo_games", "whole"),
        ("min_attack_sorties", "whole"),
        ("min_time_on_target_minutes", "number"),
        ("min_air_superiority_sorties", "whole"),
        ("min_air_superiority_minutes", "number"),
    ]
    for name, kind in minimums:
        # the Elo minimum also picks the tours the stored all-time Elo may come from (`RatingRules.min_games`)
        effect: Effect = "rescore" if name == "min_elo_games" else "display"
        fields.append(RuleField("score", name, kind, effect, "leaderboards"))
    fields.append(RuleField("marks", "min_sorties", "positive_whole", "display", "leaderboards"))
    return tuple(fields)


FIELDS: Final = _build()
BY_KEY: Final = {f.key: f for f in FIELDS}

LEADERBOARD_MINIMUMS: Final = tuple(f.name for f in FIELDS if f.page == "leaderboards" and f.section == "score")


def fields_of(page: Page) -> tuple[RuleField, ...]:
    return tuple(f for f in FIELDS if f.page == page)


def base_value(rules: RuleSet, field: RuleField) -> object:
    """What `rules` (the file's, or the built-in defaults) say for this field, in the shape the parser reads."""
    match field.section:
        case "score" if field.name in LEADERBOARD_MINIMUMS:
            return getattr(rules.leaderboards, field.name)
        case "score":
            return getattr(rules.score, field.name)
        case "ratings":
            return getattr(rules.ratings, field.name)
        case "killboard":
            return rules.board.assists
        case "marks":
            return rules.marks.min_sorties
        case "rules":
            return getattr(rules.replay.toggles, field.name)
        case "replay":
            return getattr(rules.replay, field.name)
        case _:  # tours
            match field.name:
                case "mode":
                    return rules.tours.label
                case "start":
                    return rules.tours.start.isoformat() if rules.tours.start is not None else ""
                case _:
                    return rules.tours.timezone_name


def overlay(base: RuleSet, overrides: Mapping[str, object]) -> RuleSet:
    """`base` with the admin's values on top, read by the file's own parser. Raises `ConfigError` when they are invalid
    (also in combination: days:N needs a start date). Unknown keys are ignored."""
    raw: dict[str, dict[str, object]] = {}
    for field in FIELDS:
        value = overrides.get(field.key, base_value(base, field))
        raw.setdefault(field.section, {})[field.name] = value
    return load_rule_set_from(raw, base.tours.timezone_name)


def sanitize(stored: object) -> dict[str, object]:
    """The usable part of a stored `rule_settings` object: known keys with a value of the right JSON type. Never
    raises (a hand-edited row gives fewer overrides, not an error)."""
    if not isinstance(stored, dict):
        return {}
    clean: dict[str, object] = {}
    for key, value in cast(Mapping[str, object], stored).items():
        field = BY_KEY.get(key)
        if field is None:
            continue
        if field.kind == "bool":
            ok = isinstance(value, bool)
        elif field.kind in ("text", "date"):
            ok = isinstance(value, str)
        else:
            ok = isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
        if ok:
            clean[key] = value
    return clean


def effective_rules(base: RuleSet, stored: object) -> RuleSet:
    """The rules in force: `base` overlaid with the stored overrides. An override set the parser rejects as a whole
    (an older value that no longer fits a changed file) is dropped one key at a time, so a bad row can never stop the
    ingest or the site."""
    overrides = sanitize(stored)
    if not overrides:
        return base
    try:
        return overlay(base, overrides)
    except ConfigError:
        usable: dict[str, object] = {}
        for key, value in overrides.items():
            try:
                overlay(base, {**usable, key: value})
            except ConfigError:
                continue
            usable[key] = value
        return overlay(base, usable) if usable else base


def canonical(field: RuleField, text: str) -> object:
    """The typed value of a (validated, non-blank) text from the admin form."""
    text = text.strip()
    match field.kind:
        case "bool":
            return text.lower() in TRUE_WORDS
        case "whole" | "positive_whole":
            return int(float(text.replace(",", ".")))
        case "number" | "positive":
            return float(text.replace(",", "."))
        case "date":
            return date.fromisoformat(text).isoformat()
        case "text":
            return text


def validate(base: RuleSet, texts: Mapping[str, str]) -> tuple[dict[str, object], list[str]]:
    """The overrides the posted form describes (`key -> text`; blank = no override) and the problems that stop it from
    being saved. Each value is checked with the file's parser, alone first (so every wrong field is reported), then all
    together (a tour mode that needs a start date)."""
    clean: dict[str, object] = {}
    problems: list[str] = []
    for key, text in texts.items():
        field = BY_KEY.get(key)
        if field is None or not text.strip():
            continue
        sent: object = text.strip().replace(",", ".") if field.kind not in ("text", "date", "bool") else text.strip()
        if field.section != "tours":  # a tour mode and its start date are only valid together: checked below
            try:
                overlay(base, {key: sent})
            except ConfigError as exc:
                problems.append(str(exc))
                continue
        try:
            clean[key] = canonical(field, text)
        except ValueError:  # a date the parser accepted as text but is not one: the combined check names it
            clean[key] = text.strip()
    if not problems:
        try:
            overlay(base, clean)
        except ConfigError as exc:
            problems.append(str(exc))
    return clean, problems
