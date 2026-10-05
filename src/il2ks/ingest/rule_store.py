"""The admin's overrides of the game rules in the database: wanted vs applied (the pair of the achievements and of the
flight-time option), and the rules that are in force (maintainer decision 2026-10-05; `il2ks.rule_settings` says which
settings and how they are read).

`SiteSettings.rule_settings` is what the admin chose, `rule_settings_applied` what the stored numbers were last computed
with. Every code path that turns the configuration into rule objects (ingest, reprocess, rebuild, live, the pages) goes
through `effective_config` (or `il2ks.rule_settings.effective_rules` on a `RuleSet`), which overlays the APPLIED values
on the file's, so incremental == rebuild. The effects: `display` and `reprocess` fields are written to both at once;
`rescore` and `retour` fields only to the wanted side, and `rescore_with_wanted` (`watch`) or `rebuild-aggregates`
adopts them together with a level-2 rebuild.
"""

import dataclasses
import logging
from collections.abc import Iterable, Mapping

from django.db import DatabaseError, transaction

from il2ks.config import Config, RuleSet
from il2ks.db.models import SiteSettings
from il2ks.db.site import bump_data_version, get_site_settings
from il2ks.ingest.flight_score import flight_score_pending
from il2ks.ingest.stat_marks import recompute_thresholds
from il2ks.ingest.tours import on_win_pending
from il2ks.rule_settings import BY_KEY, FIELDS, Effect, RuleField, effective_rules, sanitize

log = logging.getLogger(__name__)

MARK_KEYS = frozenset(
    {
        "marks.min_sorties",
        "score.min_time_on_target_minutes",
        "score.min_air_superiority_minutes",
    }
)
"""The overrides the stored stat thresholds (the marks) are computed from."""


def config_with(cfg: Config, overrides: Mapping[str, object]) -> Config:
    """`cfg` with these overrides on its game rules."""
    rules = effective_rules(RuleSet.of(cfg), overrides)
    return dataclasses.replace(
        cfg,
        replay=rules.replay,
        ratings=rules.ratings,
        marks=rules.marks,
        score=rules.score,
        leaderboards=rules.leaderboards,
        board=rules.board,
        tours=rules.tours,
    )


def _stored(column: str) -> dict[str, object]:
    raw = SiteSettings.objects.filter(pk=1).values_list(column, flat=True).first()
    return sanitize(raw)


def wanted_overrides() -> dict[str, object]:
    return _stored("rule_settings")


def applied_overrides() -> dict[str, object]:
    return _stored("rule_settings_applied")


def effective_config(cfg: Config) -> Config:
    """The rules in force: the file's (and defaults') overlaid with the APPLIED admin values. Every ingest, reprocess,
    rebuild and live path reads its rules through this. A database that cannot be read (before the update) gives
    `cfg` unchanged."""
    try:
        applied = applied_overrides()
    except DatabaseError:
        return cfg
    return config_with(cfg, applied) if applied else cfg


def pending_fields() -> list[RuleField]:
    """The rule fields whose chosen value is not applied yet (only `rescore` and `retour` ones wait)."""
    wanted, applied = wanted_overrides(), applied_overrides()
    return [f for f in FIELDS if f.effect in ("rescore", "retour") and wanted.get(f.key) != applied.get(f.key)]


def pending_effects() -> frozenset[Effect]:
    """What has to run before everything the admin chose is applied: `rescore` (re-score and rebuild level 2) and/or
    `retour` (reassign the missions to tours, then rebuild). Includes the flight-time option and the tour switch."""
    effects: set[Effect] = {f.effect for f in pending_fields()}
    if flight_score_pending():
        effects.add("rescore")
    if on_win_pending():
        effects.add("retour")
    return frozenset(effects)


def rebuild_overrides(*, reassign_tours: bool) -> dict[str, object]:
    """The overrides a full rebuild computes with, and then adopts as applied: every wanted value, except the tour
    settings unless the missions are reassigned in the same rebuild (`--retour`)."""
    wanted, applied = wanted_overrides(), applied_overrides()
    merged = dict(wanted)
    if not reassign_tours:
        for field in FIELDS:
            if field.effect == "retour":
                merged.pop(field.key, None)
                if field.key in applied:
                    merged[field.key] = applied[field.key]
    return merged


def _locked_row() -> SiteSettings:
    """The settings row, locked until the end of the transaction (a no-op on SQLite, which has one writer): the admin's
    save and the rebuild's adopt step both read it, change it and write it back."""
    get_site_settings()
    return SiteSettings.objects.select_for_update().get(pk=1)


def adopt_overrides(overrides: Mapping[str, object]) -> None:
    """Record these as the ones the stored numbers were computed with (in the rebuild's transaction).

    `overrides` were read when the rebuild started, which can take minutes; the admin may have saved a `display` or
    `reprocess` value meanwhile (written to both sides at once). Those keys are taken from the row as it is now, under
    its lock, so the adopt step cannot undo a save (a lost update). The `rescore` and `retour` keys are what the
    rebuild computed with."""
    row = _locked_row()
    current = sanitize(row.rule_settings_applied)
    merged = {key: value for key, value in overrides.items() if BY_KEY[key].effect in ("rescore", "retour")}
    for field in FIELDS:
        if field.effect in ("display", "reprocess") and field.key in current:
            merged[field.key] = current[field.key]
    row.rule_settings_applied = merged
    row.save(update_fields=["rule_settings_applied"])


def save_overrides(fields: Iterable[RuleField], overrides: Mapping[str, object], base: RuleSet) -> bool:
    """Store the admin's values for these fields (`overrides` holds the ones that are set; the others go back to the
    file's value). `display` and `reprocess` fields apply at once; the stat marks follow board minimums in the same
    transaction. Returns whether the stored thresholds were rewritten."""
    fields = list(fields)
    with transaction.atomic():
        row = _locked_row()
        wanted, applied = sanitize(row.rule_settings), sanitize(row.rule_settings_applied)
        marks_changed = False
        for field in fields:
            key = field.key
            if key in overrides:
                wanted[key] = overrides[key]
            else:
                wanted.pop(key, None)
            if field.effect in ("display", "reprocess"):
                if applied.get(key) != wanted.get(key) and key in MARK_KEYS:
                    marks_changed = True
                if key in wanted:
                    applied[key] = wanted[key]
                else:
                    applied.pop(key, None)
        row.rule_settings = wanted
        row.rule_settings_applied = applied
        row.save(update_fields=["rule_settings", "rule_settings_applied", "updated_at"])
        if marks_changed:
            recompute_thresholds(effective_rules(base, applied).marks)
        bump_data_version()
    return marks_changed


def admin_rule_lines() -> list[str]:
    """`key = value` of every game rule the admin set (`il2ks doctor`): they win over `il2ks.toml`."""
    return [f"{key} = {value}" for key, value in applied_overrides().items()]
