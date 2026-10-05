"""The admin site: titles from `SiteSettings`, the ingestion status page and the "reprocess all missions" request
(FR-ADM-1, FR-ADM-2, FR-ADM-4). The request is only filed here; the `watch` loop runs it (doc 14)."""

from pathlib import Path

from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import URLPattern, URLResolver, path, reverse
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext as _

from il2ks.db.models import Mission, SiteSettings
from il2ks.db.reprocess_requests import AlreadyPendingError, request_reprocess
from il2ks.db.site import bump_data_version, get_site_settings
from il2ks.serving import custom
from il2ks.web import admin_achievements as achievement_forms
from il2ks.web import quips
from il2ks.web.achievement_config import MAX_DESCRIPTION, MAX_NAME, AchievementConfig
from il2ks.web.admin_quips import Draft, build_rows, mode_labels, parse_form, rejected_drafts
from il2ks.web.ingest_status import build_overview
from il2ks.web.quips import QuipConfig


class Il2ksAdminSite(admin.AdminSite):
    index_template = "admin/il2ks_index.html"

    def each_context(self, request: HttpRequest) -> dict[str, object]:
        """Header and page titles follow the site title the admin set."""
        row = SiteSettings.objects.filter(pk=1).only("site_title").first()  # read-only: the login page uses this too
        title = row.site_title if row is not None else SiteSettings().site_title
        context = super().each_context(request)
        context.update(
            site_header=title, site_title=_("%(title)s admin") % {"title": title}, index_title=_("Site administration")
        )
        # TD-25: staff see a red banner on every admin page while a file in custom/ is based on an old or unknown
        # template version. Computed once per process (overrides only change at a restart); never on public pages.
        context["custom_problems"] = custom.startup_problems(Path(settings.CUSTOM_DIR)) if request.user.is_staff else []
        return context

    def get_urls(self) -> list[URLPattern | URLResolver]:  # pyright: ignore[reportIncompatibleMethodOverride]
        extra = [
            path("ingestion/", self.admin_view(self.ingest_status_view), name="ingest-status"),
            path("quips/", self.admin_view(self.quips_view), name="quips"),
            path("achievements/", self.admin_view(self.achievements_view), name="achievements"),
            path("tours/", self.admin_view(self.tours_view), name="tours"),
            path("ingestion/reprocess/", self.admin_view(self.reprocess_all_view), name="reprocess-all"),
        ]
        return [*extra, *super().get_urls()]

    def ingest_status_view(self, request: HttpRequest) -> HttpResponse:
        if not request.user.has_perm("il2ks_db.view_ingestrun"):
            raise PermissionDenied
        context = {
            **self.each_context(request),
            "title": _("Ingestion status"),
            "overview": build_overview(timezone.now()),
        }
        return TemplateResponse(request, "admin/il2ks_ingest_status.html", context)

    def reprocess_all_view(self, request: HttpRequest) -> HttpResponse:
        """Confirmation page (GET) and the request itself (POST). Needs the permission to add a `ReprocessRequest`
        (superusers have it; other staff only when granted). Nothing runs here: the `watch` loop picks it up."""
        if not request.user.has_perm("il2ks_db.add_reprocessrequest"):
            raise PermissionDenied
        status_url = reverse(f"{self.name}:ingest-status")
        if request.method == "POST":
            try:
                request_reprocess(request.user.get_username(), timezone.now())
            except AlreadyPendingError:
                messages.warning(request, _("A reprocess request is already waiting."))
            else:
                messages.success(
                    request, _("Reprocessing of all missions is requested. It starts at the next watch tick.")
                )
            return HttpResponseRedirect(status_url)
        overview = build_overview(timezone.now())
        if not overview.can_request_reprocess:
            messages.warning(request, _("A reprocess request is already waiting or running."))
            return HttpResponseRedirect(status_url)
        context = {
            **self.each_context(request),
            "title": _("Reprocess all missions"),
            "missions_stored": overview.missions_stored,
            "status_url": status_url,
        }
        return TemplateResponse(request, "admin/il2ks_reprocess_confirm.html", context)

    def achievements_view(self, request: HttpRequest) -> HttpResponse:
        """The Achievements page (FR-WEB-26): switch, words and thresholds of every achievement. Words and switches
        apply at once; thresholds (and switching one back on) when `watch` has recomputed the rows (the page says)."""
        if not request.user.has_perm("il2ks_db.change_sitesettings"):
            raise PermissionDenied
        row = get_site_settings()
        config = AchievementConfig.from_row(row)
        pending = config.wanted != config.applied  # of the stored choice, not of a rejected form
        typed: dict[str, tuple[str, ...]] = {}
        if request.method == "POST":
            posted, errors, typed = achievement_forms.parse_form(request.POST, config)
            if not errors:
                with transaction.atomic():
                    row.achievements = posted.to_json()
                    row.save(update_fields=["achievements", "updated_at"])
                    bump_data_version()  # cached pages refresh (TD-28)
                messages.success(request, _("Achievements saved."))
                return HttpResponseRedirect(reverse(f"{self.name}:achievements"))
            for error in errors:
                messages.error(request, error)
            config = posted  # the form comes back as typed (nothing is lost)
        context = {
            **self.each_context(request),
            "title": _("Achievements"),
            "rows": achievement_forms.build_rows(config, typed),
            "pending": pending,
            "max_name": MAX_NAME,
            "max_description": MAX_DESCRIPTION,
        }
        return TemplateResponse(request, "admin/il2ks_achievements.html", context)

    def tours_view(self, request: HttpRequest) -> HttpResponse:
        """The Tours page: "start a new tour when a mission is won by one side" (default off). Saving only records the
        choice (`SiteSettings.tour_on_win`); `watch` (or `rebuild-aggregates --retour`) reassigns the missions and
        records it as applied (`ingest.tours`), and the page says so while the two differ."""
        if not request.user.has_perm("il2ks_db.change_sitesettings"):
            raise PermissionDenied
        row = get_site_settings()
        if request.method == "POST":
            row.tour_on_win = request.POST.get("tour_on_win") == "on"
            with transaction.atomic():
                row.save(update_fields=["tour_on_win", "updated_at"])
                bump_data_version()
            messages.success(request, _("Tour settings saved."))
            return HttpResponseRedirect(reverse(f"{self.name}:tours"))
        results = Mission.objects.values("result").annotate(n=Count("pk"))
        counts = {r["result"]: r["n"] for r in results}
        won = Mission.objects.filter(winning_coalition__isnull=False).count()
        context = {
            **self.each_context(request),
            "title": _("Tours"),
            "wanted": row.tour_on_win,
            "pending": row.tour_on_win != row.tour_on_win_applied,
            "won": won,
            "draws": counts.get("draw", 0),
            "unknown": sum(counts.values()) - won - counts.get("draw", 0),
        }
        return TemplateResponse(request, "admin/il2ks_tours.html", context)

    def quips_view(self, request: HttpRequest) -> HttpResponse:
        """The Quips page (FR-WEB-23): the global switch, every spot's mode, built-in lines (hide) and own lines."""
        if not request.user.has_perm("il2ks_db.change_sitesettings"):
            raise PermissionDenied
        row = get_site_settings()
        config = QuipConfig.from_row(row.quips_enabled, row.quips)
        drafts: dict[str, Draft] = {}
        if request.method == "POST":
            posted, errors = parse_form(request.POST, config)
            if not errors:
                with transaction.atomic():
                    row.quips_enabled = posted.enabled
                    row.quips = posted.to_json()
                    row.save(update_fields=["quips_enabled", "quips", "updated_at"])
                    bump_data_version()  # cached pages refresh (TD-28)
                messages.success(request, _("Quips saved."))
                return HttpResponseRedirect(reverse(f"{self.name}:quips"))
            for error in errors:
                messages.error(request, error)
            config = posted  # the form comes back as posted, with the rejected lines (nothing typed is lost)
            drafts = rejected_drafts(request.POST, posted)
        context = {
            **self.each_context(request),
            "title": _("Quips"),
            "enabled": config.enabled,
            "rows": build_rows(config, get_language() or "en", drafts),
            "mode_options": list(mode_labels().items()),
            "languages": list(settings.LANGUAGES),
            "max_length": quips.MAX_LEN,
        }
        return TemplateResponse(request, "admin/il2ks_quips.html", context)
