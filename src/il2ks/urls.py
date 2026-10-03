from django.conf import settings
from django.contrib import admin
from django.urls import URLPattern, URLResolver, include, path

from il2ks.web import media

urlpatterns: list[URLPattern | URLResolver] = [
    path("admin/", admin.site.urls),
    path(f"{settings.MEDIA_URL.strip('/')}/<path:path>", media.serve_media, name="media"),
    path("", include("il2ks.web.urls")),
]
