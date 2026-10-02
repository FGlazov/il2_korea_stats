from django.apps import AppConfig


class DbConfig(AppConfig):
    name = "il2ks.db"
    label = "il2ks_db"
    default_auto_field = "django.db.models.BigAutoField"
