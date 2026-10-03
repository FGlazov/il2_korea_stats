"""Singleton access for `SiteSettings` and `DataVersion` (TD-25, TD-28)."""

from django.db.models import F
from django.db.models.functions import Now

from il2ks.db.models import DataVersion, SiteSettings


def get_site_settings() -> SiteSettings:
    """The site's branding row, created with defaults on first use."""
    settings, _ = SiteSettings.objects.get_or_create(pk=1)
    return settings


def current_data_version() -> int:
    row = DataVersion.objects.filter(pk=1).only("version").first()
    return row.version if row is not None else 0


def bump_data_version() -> None:
    """Call inside the transaction that changes page-visible data (mission save, rebuild, admin edits)."""
    # `update()` skips `auto_now`, so the timestamp (the footer's "Data updated") is set explicitly.
    if DataVersion.objects.filter(pk=1).update(version=F("version") + 1, updated_at=Now()) == 0:
        DataVersion.objects.create(pk=1, version=1)
