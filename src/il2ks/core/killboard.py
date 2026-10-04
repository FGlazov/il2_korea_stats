"""Killboard rules (FR-WEB-9). Pure data, no database."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class KillboardRules:
    """The `[killboard]` config section. Level 2: a change takes effect with `il2ks rebuild-aggregates`."""

    assists: bool = False  # also count assist credits (own column, never mixed into kills)


DEFAULT_KILLBOARD_RULES = KillboardRules()
