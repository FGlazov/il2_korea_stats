from django.contrib.admin.apps import AdminConfig


class Il2ksAdminConfig(AdminConfig):
    """`django.contrib.admin` with our `AdminSite` (title from SiteSettings, ingestion status page). Not in `apps.py`:
    Django would see two default AppConfigs there."""

    default_site = "il2ks.web.admin_site.Il2ksAdminSite"
