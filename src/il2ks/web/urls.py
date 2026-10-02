from django.urls import URLPattern, path

from il2ks.web import views

app_name = "web"
urlpatterns: list[URLPattern] = [
    path("", views.home, name="home"),
]
