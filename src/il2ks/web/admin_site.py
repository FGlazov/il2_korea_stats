"""The admin site: titles from `SiteSettings` and the ingestion status page (FR-ADM-1, FR-ADM-2, FR-ADM-4)."""

from pathlib import Path

from django.conf import settings
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.urls import URLPattern, URLResolver, path
from django.utils import timezone
from django.utils.translation import gettext as _

from il2ks.db.models import SiteSettings
from il2ks.serving import custom
from il2ks.web.ingest_status import build_overview


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
        extra = [path("ingestion/", self.admin_view(self.ingest_status_view), name="ingest-status")]
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
