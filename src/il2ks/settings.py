"""Django settings.

Configuration comes from environment variables for now (TD-11 plans an `il2ks.toml` written by `il2ks setup`).
SQLite is the only user-facing database. Postgres is dev-side only, selected with IL2KS_TEST_DB=postgres (TD-04, TD-19).
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("IL2KS_DATA_DIR", Path.cwd() / ".il2ks-data"))

SECRET_KEY = os.environ.get("IL2KS_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = os.environ.get("IL2KS_DEBUG", "0") == "1"
ALLOWED_HOSTS = [h for h in os.environ.get("IL2KS_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "il2ks.db",
    "il2ks.web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "il2ks.urls"
WSGI_APPLICATION = "il2ks.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # custom/ overrides come first (TD-25).
        "DIRS": [DATA_DIR / "custom" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


def _databases() -> dict[str, dict[str, object]]:
    sqlite: dict[str, object] = {"ENGINE": "django.db.backends.sqlite3", "NAME": DATA_DIR / "il2ks.sqlite3"}
    if os.environ.get("IL2KS_TEST_DB") != "postgres":
        return {"default": sqlite}
    postgres: dict[str, object] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("IL2KS_PG_NAME", "il2ks"),
        "USER": os.environ.get("IL2KS_PG_USER", "il2ks"),
        "PASSWORD": os.environ.get("IL2KS_PG_PASSWORD", "il2ks"),
        "HOST": os.environ.get("IL2KS_PG_HOST", "127.0.0.1"),
        "PORT": os.environ.get("IL2KS_PG_PORT", "5432"),
    }
    # Postgres is the default under test; SQLite stays available as "sqlite" for the transfer test (TD-19).
    return {"default": postgres, "sqlite": sqlite}


DATABASES = _databases()
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English")]  # it2 adds ru, de, es, fr, pt-br (TD-24)
USE_I18N = True
USE_TZ = True
TIME_ZONE = "UTC"

STATIC_URL = "static/"
STATIC_ROOT = DATA_DIR / "staticfiles"
STATICFILES_DIRS = [DATA_DIR / "custom" / "static"] if (DATA_DIR / "custom" / "static").is_dir() else []
