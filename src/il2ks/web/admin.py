"""Django admin registrations (FR-ADM-1..5).

Lives in the web app (the admin is presentation; models stay in `il2ks.db`). Rules:

- Rows that ingest owns (players, missions, sorties, counters, tours, ingestion runs) are read-only apart from
  `is_hidden` (and a tour's title):
  anything else would be overwritten by the next ingest or rebuild. They can't be added or deleted here.
- Every save or action on a page-visible model bumps the data version in the same transaction (TD-28), so cached pages
  are revalidated. (`changeform_view` and the changelist's `list_editable` save run inside a transaction; the bulk
  actions open their own.)
"""

from collections.abc import Sequence
from datetime import date
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db import models, transaction
from django.forms import ModelForm
from django.forms.models import BaseModelFormSet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import URLPattern, path, reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from django.utils.translation import gettext, ngettext
from django.utils.translation import gettext_lazy as _

from il2ks.core.catalog.loader import load_default_catalog
from il2ks.db.models import (
    Counters,
    Country,
    GameObject,
    IngestRun,
    Mission,
    NavLink,
    Player,
    PlayerTour,
    SiteSettings,
    Tour,
)
from il2ks.db.site import bump_data_version, get_site_settings
from il2ks.ingest.achievements import recompute_holders
from il2ks.ingest.activity import day_of, recompute_days
from il2ks.ingest.tours import start_manual_tour
from il2ks.web.fonts import RECOMMENDED_FONT_BYTES, clean_fonts, font_face_css, prune_fonts
from il2ks.web.logo import prune_logos, store_bytes, store_logo
from il2ks.web.object_names import default_catalog
from il2ks.web.site_forms import MAX_NAV_LINKS, RECOMMENDED_NAV_LINKS, NavLinkFormSet, SiteSettingsForm
from il2ks.web.theme import ContrastWarning, contrast_warnings

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


class NavLinkInline(admin.TabularInline):  # pyright: ignore[reportMissingTypeArgument]
    model = NavLink
    formset = NavLinkFormSet
    extra = 2
    max_num = MAX_NAV_LINKS
    fields = ("label", "url", "icon", "position")
    verbose_name = _("navigation link")
    verbose_name_plural = _("Navigation links, in order")


def _contrast_message(item: ContrastWarning) -> str:
    return gettext(
        "Low contrast in %(mode)s mode: %(what)s has a contrast ratio of %(ratio)s:1; aim for at least %(minimum)s:1 "
        "so it stays readable."
    ) % {
        "mode": gettext("light") if item.mode == "light" else gettext("dark"),
        "what": item.what,
        "ratio": item.ratio,
        "minimum": item.minimum,
    }


@admin.register(SiteSettings)
class SiteSettingsAdmin(ModelAdmin[SiteSettings]):
    form = SiteSettingsForm
    inlines = (NavLinkInline,)
    readonly_fields = ("current_logo", "uploaded_fonts", "nav_links_help")
    fieldsets = (
        (None, {"fields": ("site_title", "server_name", "description")}),
        (_("Logo"), {"fields": ("current_logo", "logo_upload", "remove_logo")}),
        (
            _("Fonts"),
            {
                "fields": (
                    "uploaded_fonts",
                    "font_upload",
                    "font_use",
                    "remove_fonts",
                    "heading_font",
                    "body_font",
                )
            },
        ),
        (_("Colors"), {"fields": ("theme_preset", "theme")}),
        (_("Navigation links"), {"fields": ("nav_links_help",)}),
        (
            _("Coalitions"),
            {
                "fields": ("redfor_name", "blufor_name", "redfor_emblem", "blufor_emblem"),
                "description": _("Names and emblems shown for 5xx / 6xx. The default emblems are neutral."),
            },
        ),
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

    @admin.display(description=_("Uploaded fonts"))
    def uploaded_fonts(self, obj: SiteSettings) -> str:
        """The uploaded fonts with a preview line each (the `@font-face` rules come from the validated list)."""
        fonts = clean_fonts(obj.custom_fonts)
        if not fonts:
            return _("No uploaded fonts.")
        # Translators: preview text for a font; use a pangram of the target language (every letter of the alphabet)
        sample = _("The quick brown fox jumps over the lazy dog 0123456789")
        rows = format_html_join(
            "",
            '<p style="margin:.4em 0"><strong>{}</strong><br>'
            '<span style="font-family:{},sans-serif;font-size:1.4em">{}</span></p>',
            ((font.label, font.family, sample) for font in fonts),
        )
        return format_html("<style>{}</style>{}", mark_safe(font_face_css(fonts, settings.MEDIA_URL)), rows)

    @admin.display(description=_("About the links"))
    def nav_links_help(self, obj: SiteSettings) -> str:
        return format_html(
            "{} {} {}",
            _(
                "Extra links for the top navigation bar (Discord, a forum, Patreon, ...), "
                "shown after the built-in ones."
            ),
            _("Lower numbers come first. Links open in a new tab; only http:// and https:// addresses are accepted."),
            ngettext(
                "We recommend at most %(n)d extra link with a short label (one or two words): more still work, "
                "but the menu then wraps onto a second row of the header.",
                "We recommend at most %(n)d extra links with short labels (one or two words): more still work, "
                "but the menu then wraps onto a second row of the header.",
                RECOMMENDED_NAV_LINKS,
            )
            % {"n": RECOMMENDED_NAV_LINKS},
        )

    def save_related(self, request: HttpRequest, form: ModelForm, formsets: BaseModelFormSet, change: bool) -> None:
        super().save_related(request, form, formsets, change)
        # Number the links 1..n in the order shown and publish them into the settings row (pages read that copy).
        site = form.instance
        assert isinstance(site, SiteSettings)
        links = list(NavLink.objects.filter(site=site))
        for number, link in enumerate(links, start=1):
            if link.position != number:
                link.position = number
                link.save(update_fields=["position"])
        site.links = [{"label": link.label, "url": link.url, "icon": link.icon} for link in links]
        site.save(update_fields=["links", "updated_at"])
        bump_data_version()

    def save_model(self, request: HttpRequest, obj: SiteSettings, form: ModelForm, change: bool) -> None:
        assert isinstance(form, SiteSettingsForm)
        for item in contrast_warnings(obj.theme):
            messages.warning(request, _contrast_message(item))
        media_root = Path(settings.MEDIA_ROOT)
        if form.processed_logo is not None:
            store_logo(form.processed_logo, media_root)
            obj.logo = form.processed_logo.name
        elif form.cleaned_data["remove_logo"]:
            obj.logo = ""
        fonts = form.kept_fonts
        if form.processed_font is not None:
            store_bytes(form.processed_font.data, form.processed_font.font.file, media_root)
            if len(form.processed_font.data) > RECOMMENDED_FONT_BYTES:
                messages.warning(
                    request,
                    _(
                        "The uploaded font is %(kb)d KB. Every visitor downloads it once (then it is cached), so "
                        "large fonts slow down the first page view; a subset .woff2 under 100 KB is ideal."
                    )
                    % {"kb": len(form.processed_font.data) // 1024},
                )
        obj.custom_fonts = [font.as_json() for font in fonts]
        super().save_model(request, obj, form, change)
        bump_data_version()
        # Old logo files go only once the new row is committed (a rolled-back save keeps the logo it had), and a file
        # that cannot be deleted right now (Windows refuses while it is being served) is left for the next save.
        transaction.on_commit(partial(prune_logos, media_root, obj.logo))
        transaction.on_commit(partial(prune_fonts, media_root, [font.file for font in fonts]))


# --- Players and missions: read-only apart from hiding (FR-ADM-3) ---


def _set_hidden(
    admin_obj: ReadOnlyIngestedAdmin[Player] | ReadOnlyIngestedAdmin[Mission],
    request: HttpRequest,
    queryset: models.QuerySet[Player] | models.QuerySet[Mission],
    hidden: bool,
) -> None:
    with transaction.atomic():
        toggled = queryset.exclude(is_hidden=hidden)
        # Hiding a mission changes its day's activity numbers (FR-WEB-16)
        days: set[date] = set()
        if queryset.model is Mission:
            days = {day_of(m.started_at) for m in toggled if isinstance(m, Mission)}
        changed = toggled.update(is_hidden=hidden)
        recompute_days(days)
        if queryset.model is Player:
            recompute_holders()  # FR-WEB-26: the medal overview counts visible players only
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
    list_filter = ("is_hidden", "completed_cleanly", "tour", "started_at")
    search_fields = ("mission_uid", "mission_file")
    ordering = ("-started_at",)
    actions = ("hide_selected", "unhide_selected")
    fieldsets = (
        (
            None,
            {"fields": ("is_hidden", "mission_uid", "mission_file", "tour", "started_at", "ended_at", "duration_s")},
        ),
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

    def save_model(self, request: HttpRequest, obj: Mission, form: ModelForm, change: bool) -> None:
        super().save_model(request, obj, form, change)
        recompute_days({day_of(obj.started_at)})  # hiding or showing it changes its day's activity (FR-WEB-16)

    @admin.action(description=_("Hide selected missions from public pages"), permissions=["change"])
    def hide_selected(self, request: HttpRequest, queryset: models.QuerySet[Mission]) -> None:
        _set_hidden(self, request, queryset, True)

    @admin.action(description=_("Show selected missions on public pages again"), permissions=["change"])
    def unhide_selected(self, request: HttpRequest, queryset: models.QuerySet[Mission]) -> None:
        _set_hidden(self, request, queryset, False)


# --- Tours (FR-ADM-8, TD-26) ---


@admin.register(Tour)
class TourAdmin(ReadOnlyIngestedAdmin[Tour]):
    """Rename tours; in manual mode, start the next one. Boundaries and mode are the ingester's."""

    editable = ("title",)
    list_display = ("title", "started_at", "ended_at", "mode", "missions_count")
    list_display_links = ("title",)
    ordering = ("-started_at",)
    change_list_template = "admin/il2ks_db/tour/change_list.html"

    def get_queryset(self, request: HttpRequest) -> models.QuerySet[Tour]:
        return super().get_queryset(request).annotate(missions_n=models.Count("missions"))

    @admin.display(description=_("Missions"), ordering="missions_n")
    def missions_count(self, obj: Tour) -> int:
        return int(getattr(obj, "missions_n", 0))

    def changelist_view(self, request: HttpRequest, extra_context: dict[str, str] | None = None) -> TemplateResponse:
        manual = getattr(settings, "IL2KS_TOUR_MODE", "monthly") == "manual"
        can_start = "1" if manual and self.has_change_permission(request) else ""
        return super().changelist_view(request, {**(extra_context or {}), "can_start_tour": can_start})

    def get_urls(self) -> list[URLPattern]:
        start = path("start/", self.admin_site.admin_view(self.start_view), name="il2ks_db_tour_start")
        return [start, *super().get_urls()]

    def start_view(self, request: HttpRequest) -> HttpResponseRedirect:
        """POST: close the open tour and start a new one now (manual mode only)."""
        changelist = reverse("admin:il2ks_db_tour_changelist")
        if request.method != "POST" or not self.has_change_permission(request):
            raise PermissionDenied
        if getattr(settings, "IL2KS_TOUR_MODE", "monthly") != "manual":
            self.message_user(request, _("Tours start by themselves unless [tours] mode is manual."), messages.ERROR)
            return HttpResponseRedirect(changelist)
        with transaction.atomic():
            tour = start_manual_tour()
            bump_data_version()
        self.message_user(request, _("Started %(title)s.") % {"title": tour.title}, messages.SUCCESS)
        return HttpResponseRedirect(changelist)


@admin.register(PlayerTour)
class PlayerTourAdmin(ModelAdmin[PlayerTour]):
    """A player's counters in one tour: derived data, read only."""

    list_display = ("player", "tour", "sorties", "kills_air", "kills_ground", "deaths")
    list_filter = ("tour",)
    search_fields = ("player__current_name", "player__account_uuid")
    list_select_related = ("player", "tour")
    ordering = ("-tour__started_at", "-kills_air")

    def get_readonly_fields(self, request: HttpRequest, obj: PlayerTour | None = None) -> list[str]:
        return _field_names(PlayerTour)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: PlayerTour | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: PlayerTour | None = None) -> bool:
        return False


# --- Catalog and country names (FR-ADM-4, FR-ADM-5, TD-24) ---


@admin.register(GameObject)
class GameObjectAdmin(ReadOnlyIngestedAdmin[GameObject]):
    editable = ("display_name",)
    list_display = ("log_name", "display_name", "name_overridden", "cls", "propulsion", "is_playable", "is_known")
    list_editable = ("display_name",)
    list_display_links = ("log_name",)
    list_filter = ("cls", "is_playable", "is_known", "propulsion", "name_overridden")
    search_fields = ("log_name", "display_name")
    ordering = ("log_name",)
    actions = ("reset_names",)

    def save_model(self, request: HttpRequest, obj: GameObject, form: ModelForm, change: bool) -> None:
        """An edited name is an override (TD-24): English-only text that then shows in every language. Typing the
        shipped default back is no override, so the translations show again."""
        default = default_catalog().lookup(obj.log_name).display_name or obj.log_name
        obj.display_name = obj.display_name.strip()
        obj.name_overridden = obj.display_name not in {default, obj.log_name}
        super().save_model(request, obj, form, change)

    @admin.action(description=_("Reset display names to the catalog defaults"), permissions=["change"])
    def reset_names(self, request: HttpRequest, queryset: models.QuerySet[GameObject]) -> None:
        catalog = load_default_catalog()
        objects = list(queryset)
        for obj in objects:
            obj.display_name = catalog.lookup(obj.log_name).display_name or obj.log_name
            obj.name_overridden = False
        with transaction.atomic():
            GameObject.objects.bulk_update(objects, ["display_name", "name_overridden"])
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
