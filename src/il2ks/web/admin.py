"""Django admin registrations (FR-ADM-1..5).

Lives in the web app (the admin is presentation; models stay in `il2ks.db`). Rules:

- Rows that ingest owns (players, missions, sorties, counters, ingestion runs) are read-only apart from `is_hidden`:
  anything else would be overwritten by the next ingest or rebuild. They can't be added or deleted here.
- Every save or action on a page-visible model bumps the data version in the same transaction (TD-28), so cached pages
  are revalidated. (`changeform_view` and the changelist's `list_editable` save run inside a transaction; the bulk
  actions open their own.)
"""

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db import models, transaction
from django.forms import ModelForm
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from il2ks.core.catalog.loader import load_default_catalog
from il2ks.db.models import (
    Counters,
    Country,
    GameObject,
    IngestRun,
    Mission,
    Player,
    SiteSettings,
)
from il2ks.db.site import bump_data_version, get_site_settings
from il2ks.web.logo import delete_logo, store_logo
from il2ks.web.site_forms import SiteSettingsForm

if TYPE_CHECKING:
    from django.contrib.admin import ModelAdmin
else:
    # django-types makes `ModelAdmin[Model]` generic for the type checker only. A local generic subclass gives runtime
    # subscripting without patching Django (TD-16: no monkeypatching).
    class ModelAdmin[M: models.Model](admin.ModelAdmin):
        pass


def _field_names(model: type[models.Model], *, skip: Sequence[str] = ()) -> list[str]:
    return [f.name for f in model._meta.fields if f.name != "id" and f.name not in skip]


class ReadOnlyIngestedAdmin[M: models.Model](ModelAdmin[M]):
    """Base for rows ingest owns: only the fields in `editable` can be changed, nothing added or deleted. Every save
    bumps the data version (TD-28)."""

    editable: ClassVar[tuple[str, ...]] = ()

    def get_readonly_fields(self, request: HttpRequest, obj: M | None = None) -> list[str]:
        return [*super().get_readonly_fields(request, obj), *_field_names(self.model, skip=self.editable)]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: M | None = None) -> bool:
        return False

    def save_model(self, request: HttpRequest, obj: M, form: ModelForm, change: bool) -> None:
        super().save_model(request, obj, form, change)
        bump_data_version()


# --- Site settings (singleton) ---


@admin.register(SiteSettings)
class SiteSettingsAdmin(ModelAdmin[SiteSettings]):
    form = SiteSettingsForm
    readonly_fields = ("current_logo",)
    fieldsets = (
        (None, {"fields": ("site_title", "server_name", "description")}),
        (
            _("Look"),
            {"fields": ("current_logo", "logo_upload", "remove_logo", "accent_color")},
        ),
        (_("Links"), {"fields": ("links_text",)}),
        (_("Coalitions"), {"fields": ("redfor_name", "blufor_name"), "description": _("Names shown for 5xx / 6xx.")}),
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: SiteSettings | None = None) -> bool:
        return False

    def changelist_view(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, request: HttpRequest, extra_context: dict[str, str] | None = None
    ) -> HttpResponse:
        """A singleton has no list: go straight to its form (creating the row with defaults on first use)."""
        if not (self.has_view_permission(request) or self.has_change_permission(request)):
            raise PermissionDenied
        url = reverse(f"{self.admin_site.name}:il2ks_db_sitesettings_change", args=[get_site_settings().pk])
        return HttpResponseRedirect(url)

    @admin.display(description=_("Current logo"))
    def current_logo(self, obj: SiteSettings) -> str:
        if not obj.logo:
            return _("No logo.")
        return format_html(
            '<img src="{}{}" alt="" style="max-height:64px;max-width:320px;background:#ddd;padding:4px">',
            settings.MEDIA_URL,
            obj.logo,
        )

    def save_model(self, request: HttpRequest, obj: SiteSettings, form: ModelForm, change: bool) -> None:
        assert isinstance(form, SiteSettingsForm)
        previous = obj.logo
        obj.links = form.cleaned_data["links_text"]
        media_root = Path(settings.MEDIA_ROOT)
        if form.processed_logo is not None:
            store_logo(form.processed_logo, media_root)
            obj.logo = form.processed_logo.name
        elif form.cleaned_data["remove_logo"]:
            obj.logo = ""
        super().save_model(request, obj, form, change)
        bump_data_version()
        if previous and previous != obj.logo:
            delete_logo(previous, media_root)


# --- Players and missions: read-only apart from hiding (FR-ADM-3) ---


def _set_hidden(
    admin_obj: ReadOnlyIngestedAdmin[Player] | ReadOnlyIngestedAdmin[Mission],
    request: HttpRequest,
    queryset: models.QuerySet[Player] | models.QuerySet[Mission],
    hidden: bool,
) -> None:
    with transaction.atomic():
        changed = queryset.exclude(is_hidden=hidden).update(is_hidden=hidden)
        bump_data_version()
    text = (
        ngettext("%(n)d row hidden from public pages.", "%(n)d rows hidden from public pages.", changed)
        if hidden
        else ngettext("%(n)d row is visible again.", "%(n)d rows are visible again.", changed)
    )
    admin_obj.message_user(request, text % {"n": changed}, messages.SUCCESS)


@admin.register(Player)
class PlayerAdmin(ReadOnlyIngestedAdmin[Player]):
    editable = ("is_hidden",)
    list_display = (
        "current_name",
        "account_uuid",
        "sorties",
        "kills_air",
        "kills_ground",
        "deaths",
        "last_seen",
        "is_hidden",
    )
    list_filter = ("is_hidden",)
    search_fields = ("current_name", "account_uuid", "names__name")
    ordering = ("-last_seen",)
    actions = ("hide_selected", "unhide_selected")
    fieldsets = (
        (None, {"fields": ("is_hidden", "current_name", "account_uuid", "first_seen", "last_seen")}),
        (_("Counters (rebuilt by ingest)"), {"fields": _field_names(Counters)}),
        (_("Ratings"), {"fields": ("elo_prop", "elo_prop_games", "elo_jet", "elo_jet_games")}),
    )

    @admin.action(description=_("Hide selected players from public pages"), permissions=["change"])
    def hide_selected(self, request: HttpRequest, queryset: models.QuerySet[Player]) -> None:
        _set_hidden(self, request, queryset, True)

    @admin.action(description=_("Show selected players on public pages again"), permissions=["change"])
    def unhide_selected(self, request: HttpRequest, queryset: models.QuerySet[Player]) -> None:
        _set_hidden(self, request, queryset, False)


@admin.register(Mission)
class MissionAdmin(ReadOnlyIngestedAdmin[Mission]):
    editable = ("is_hidden",)
    list_display = (
        "mission_uid",
        "started_at",
        "duration_s",
        "players_total",
        "sorties_total",
        "kills_air",
        "completed_cleanly",
        "is_hidden",
    )
    list_filter = ("is_hidden", "completed_cleanly", "started_at")
    search_fields = ("mission_uid", "mission_file")
    ordering = ("-started_at",)
    actions = ("hide_selected", "unhide_selected")
    fieldsets = (
        (None, {"fields": ("is_hidden", "mission_uid", "mission_file", "started_at", "ended_at", "duration_s")}),
        (
            _("Result"),
            {
                "fields": (
                    "completed_cleanly",
                    "winning_coalition",
                    "players_total",
                    "sorties_total",
                    "redfor_sorties",
                    "blufor_sorties",
                    "kills_air",
                    "kills_ground",
                    "friendly_kills",
                )
            },
        ),
        (
            _("Source"),
            {
                "classes": ("collapse",),
                "fields": (
                    "server_uid",
                    "file_path",
                    "game_date",
                    "game_time",
                    "game_type",
                    "log_version",
                    "settings",
                    "countries",
                ),
            },
        ),
    )

    @admin.action(description=_("Hide selected missions from public pages"), permissions=["change"])
    def hide_selected(self, request: HttpRequest, queryset: models.QuerySet[Mission]) -> None:
        _set_hidden(self, request, queryset, True)

    @admin.action(description=_("Show selected missions on public pages again"), permissions=["change"])
    def unhide_selected(self, request: HttpRequest, queryset: models.QuerySet[Mission]) -> None:
        _set_hidden(self, request, queryset, False)


# --- Catalog and country names (FR-ADM-4, FR-ADM-5, TD-24) ---


@admin.register(GameObject)
class GameObjectAdmin(ReadOnlyIngestedAdmin[GameObject]):
    editable = ("display_name",)
    list_display = ("log_name", "display_name", "cls", "propulsion", "is_playable", "is_known")
    list_editable = ("display_name",)
    list_display_links = ("log_name",)
    list_filter = ("cls", "is_playable", "is_known", "propulsion")
    search_fields = ("log_name", "display_name")
    ordering = ("log_name",)
    actions = ("reset_names",)

    @admin.action(description=_("Reset display names to the catalog defaults"), permissions=["change"])
    def reset_names(self, request: HttpRequest, queryset: models.QuerySet[GameObject]) -> None:
        catalog = load_default_catalog()
        objects = list(queryset)
        for obj in objects:
            obj.display_name = catalog.lookup(obj.log_name).display_name or obj.log_name
        with transaction.atomic():
            GameObject.objects.bulk_update(objects, ["display_name"])
            bump_data_version()
        self.message_user(request, _("%(n)d names reset.") % {"n": len(objects)}, messages.SUCCESS)


@admin.register(Country)
class CountryAdmin(ReadOnlyIngestedAdmin[Country]):
    editable = ("display_name",)
    list_display = ("code", "coalition", "display_name")
    list_editable = ("display_name",)
    list_display_links = ("code",)
    list_filter = ("coalition",)
    ordering = ("code",)
    actions = ("reset_names",)

    @admin.action(description=_("Reset display names to the catalog defaults"), permissions=["change"])
    def reset_names(self, request: HttpRequest, queryset: models.QuerySet[Country]) -> None:
        catalog = load_default_catalog()
        rows = list(queryset)
        for row in rows:
            row.display_name = catalog.country_name(row.code, row.coalition)
        with transaction.atomic():
            Country.objects.bulk_update(rows, ["display_name"])
            bump_data_version()
        self.message_user(request, _("%(n)d names reset.") % {"n": len(rows)}, messages.SUCCESS)


# --- Ingestion runs: read only (FR-ADM-4) ---


@admin.register(IngestRun)
class IngestRunAdmin(ModelAdmin[IngestRun]):
    list_display = (
        "mission_uid",
        "status",
        "completion_reason",
        "started_at",
        "finished_at",
        "attempts",
        "next_retry_at",
        "lines_bad",
        "warnings_count",
    )
    list_filter = ("status", "completion_reason", "started_at")
    search_fields = ("mission_uid",)
    ordering = ("-started_at", "-pk")
    list_per_page = 50
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "mission_uid",
                    "mission",
                    "status",
                    "completion_reason",
                    "attempts",
                    "next_retry_at",
                    "started_at",
                    "finished_at",
                    "il2ks_version",
                )
            },
        ),
        (_("Problems"), {"fields": ("error_text", "warnings_list", "unknown_atypes_table", "unknown_keys_table")}),
        (
            _("Source"),
            {
                "classes": ("collapse",),
                "fields": (
                    "lines_total",
                    "lines_bad",
                    "log_version",
                    "files_list",
                    "archive_path",
                    "archive_sha256",
                    "fingerprint",
                ),
            },
        ),
    )

    def get_queryset(self, request: HttpRequest) -> models.QuerySet[IngestRun]:
        return super().get_queryset(request).defer("error", "files", "unknown_atypes", "unknown_keys")

    def get_readonly_fields(self, request: HttpRequest, obj: IngestRun | None = None) -> list[str]:
        return [
            *_field_names(IngestRun),
            "error_text",
            "warnings_list",
            "unknown_atypes_table",
            "unknown_keys_table",
            "files_list",
        ]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: IngestRun | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: IngestRun | None = None) -> bool:
        return False

    @admin.display(description=_("Warnings"))
    def warnings_count(self, obj: IngestRun) -> int:
        return len(obj.warnings)

    @admin.display(description=_("Error"))
    def error_text(self, obj: IngestRun) -> str:
        if not obj.error:
            return "-"
        return format_html(
            '<pre style="white-space:pre-wrap;max-width:100%;margin:0;padding:8px;'
            'background:var(--darkened-bg,#f5f5f5);overflow:auto">{}</pre>',
            obj.error,
        )

    @admin.display(description=_("Warnings"))
    def warnings_list(self, obj: IngestRun) -> str:
        if not obj.warnings:
            return "-"
        return format_html("<ul>{}</ul>", format_html_join("", "<li>{}</li>", ((w,) for w in obj.warnings)))

    @admin.display(description=_("Unknown event types (AType: lines)"))
    def unknown_atypes_table(self, obj: IngestRun) -> str:
        return _counts_table(obj.unknown_atypes)

    @admin.display(description=_("Unknown keys (AType:KEY: lines)"))
    def unknown_keys_table(self, obj: IngestRun) -> str:
        return _counts_table(obj.unknown_keys)

    @admin.display(description=_("Source files"))
    def files_list(self, obj: IngestRun) -> str:
        if not obj.files:
            return "-"
        return format_html_join(mark_safe("<br>"), "{}", ((name,) for name in obj.files))


def _counts_table(counts: dict[str, int]) -> str:
    if not counts:
        return "-"
    rows = format_html_join("", "<tr><td>{}</td><td>{}</td></tr>", sorted(counts.items(), key=lambda kv: -kv[1]))
    return format_html("<table>{}</table>", rows)
