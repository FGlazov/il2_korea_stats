"""Singleton access for `SiteSettings` and `DataVersion` (TD-25, TD-28)."""

import os
from datetime import UTC, datetime

from django.db.models import F
from django.db.models.functions import Now

from il2ks.db.models import DataVersion, SiteSettings


def get_site_settings() -> SiteSettings:
    """The site's branding row, created with defaults on first use."""
    settings, _ = SiteSettings.objects.get_or_create(pk=1)
    return settings


def level2_pending() -> dict[str, object]:
    """The marker of a batched level-2 run that has not finished (`SiteSettings.level2_pending`); empty = none."""
    row = SiteSettings.objects.filter(pk=1).values_list("level2_pending", flat=True).first()
    return dict(row) if row else {}


def set_level2_pending(command: str) -> None:
    """A batched run starts: from now until `clear_level2_pending`, level 2 may lag (a hard kill leaves it)."""
    get_site_settings()
    marker: dict[str, object] = {
        "since": datetime.now(UTC).isoformat(timespec="seconds"),
        "command": command,
        "pid": os.getpid(),
    }
    SiteSettings.objects.filter(pk=1).update(level2_pending=marker)


def clear_level2_pending() -> None:
    """Level 2 is complete (the batch ended, or a rebuild recomputed everything). One cheap update, only when set."""
    SiteSettings.objects.filter(pk=1).exclude(level2_pending={}).update(level2_pending={})


def current_data_version() -> int:
    row = DataVersion.objects.filter(pk=1).only("version").first()
    return row.version if row is not None else 0


def bump_data_version() -> None:
    """Call inside the transaction that changes page-visible data (mission save, rebuild, admin edits)."""
    # `update()` skips `auto_now`, so the timestamp (the footer's "Data updated") is set explicitly.
    if DataVersion.objects.filter(pk=1).update(version=F("version") + 1, updated_at=Now()) == 0:
        DataVersion.objects.create(pk=1, version=1)
