from importlib import import_module

from django.apps import AppConfig


class WebConfig(AppConfig):
    name = "il2ks.web"
    label = "il2ks_web"

    def ready(self) -> None:
        import_module("il2ks.web.login_protection")  # connects the login log receivers
