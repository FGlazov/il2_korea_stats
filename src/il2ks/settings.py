"""Django settings, derived from the loaded `Config` (TD-11): `il2ks.toml` plus `IL2KS_*` environment variables.

Django reads this module by name, so it has to load the config itself. It finds the same file the CLI found: the CLI
puts `IL2KS_CONFIG` and `IL2KS_DATA_DIR` into the environment before Django starts (and passes them on to the child
processes of `il2ks run`). The rules (hosts, HTTPS, static files) are pure functions in `il2ks.serving.djsettings`.

Development: `IL2KS_DEBUG=1` (or `debug = true`, or `il2ks web --dev`) turns on DEBUG and switches the HTTPS-only
behaviour off, so `runserver` works on plain http. SQLite is the only user-facing database. Postgres is dev-side only,
selected with IL2KS_TEST_DB=postgres (TD-04, TD-19).
"""

import os
from pathlib import Path

from il2ks.config import load_config
from il2ks.serving.djsettings import (
    INSTALLED_APPS as _APPS,
)
from il2ks.serving.djsettings import (
    LANGUAGES as _LANGUAGES,
)
from il2ks.serving.djsettings import (
    allowed_hosts,
    csrf_trusted_origins,
    security_settings,
    staticfiles_backend,
)
from il2ks.serving.secret import secret_key_for

# `serving.djsettings.INSTALLED_APPS` lists app modules (`il2ks custom` finds built-in files through them); the admin
# app is loaded through our AppConfig, which installs the site-settings aware AdminSite (FR-ADM-1).
INSTALLED_APPS = ["il2ks.web.admin_config.Il2ksAdminConfig" if app == "django.contrib.admin" else app for app in _APPS]
BASE_DIR = Path(__file__).resolve().parent
_CFG = load_config(create_server_uid=False)  # settings must not create files; `il2ks web` does
DATA_DIR = _CFG.data_dir

DEBUG = _CFG.debug
SECRET_KEY = secret_key_for(_CFG)  # the dev placeholder when none exists yet; `il2ks web` refuses to serve with it
ALLOWED_HOSTS = allowed_hosts(_CFG)
CSRF_TRUSTED_ORIGINS = csrf_trusted_origins(_CFG)

_SECURITY = security_settings(_CFG)
SECURE_PROXY_SSL_HEADER = _SECURITY.proxy_ssl_header
SECURE_SSL_REDIRECT = _SECURITY.ssl_redirect
SECURE_HSTS_SECONDS = _SECURITY.hsts_seconds
SECURE_HSTS_INCLUDE_SUBDOMAINS = _SECURITY.hsts_include_subdomains
SECURE_HSTS_PRELOAD = _SECURITY.hsts_preload
SESSION_COOKIE_SECURE = _SECURITY.session_cookie_secure
CSRF_COOKIE_SECURE = _SECURITY.csrf_cookie_secure
SESSION_COOKIE_HTTPONLY = _SECURITY.session_cookie_httponly
CSRF_COOKIE_HTTPONLY = _SECURITY.csrf_cookie_httponly
SECURE_CONTENT_TYPE_NOSNIFF = _SECURITY.content_type_nosniff
SECURE_REFERRER_POLICY = _SECURITY.referrer_policy

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "il2ks.web.caching.DataVersionCacheMiddleware",  # ETag/304 on the data version, before the view (TD-28)
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "il2ks.urls"
WSGI_APPLICATION = "il2ks.wsgi.application"

# custom/ overrides come first (TD-25, FR-ADM-6). Both folders are listed even when they don't exist yet, so one created
# later is picked up at the next start without any code change (`il2ks web` creates them).
CUSTOM_DIR = DATA_DIR / "custom"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [CUSTOM_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",  # LANGUAGE_CODE for <html lang> (TD-24)
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "il2ks.web.context_processors.site",
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
LANGUAGES = _LANGUAGES  # en + ru, de, es, fr, pt-br (TD-24)
LOCALE_PATHS = [BASE_DIR / "locale"]  # inside the package, so the wheel carries the compiled .mo files
LANGUAGE_COOKIE_AGE = 60 * 60 * 24 * 365  # the language switcher (web.views.language) remembers the choice for a year
LANGUAGE_COOKIE_SAMESITE = "Lax"
USE_I18N = True
USE_TZ = True
TIME_ZONE = "UTC"

# Static files (TD-28): WhiteNoise serves STATIC_ROOT, filled by `collectstatic` at every `il2ks web` start. Production
# uses hashed names; in debug WhiteNoise reads the source folders directly.
STATIC_URL = "static/"
STATIC_ROOT = DATA_DIR / "staticfiles"
STATICFILES_DIRS = [CUSTOM_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": staticfiles_backend(_CFG)},
}
WHITENOISE_USE_FINDERS = DEBUG
WHITENOISE_AUTOREFRESH = DEBUG
SILENCED_SYSTEM_CHECKS = ["staticfiles.W004"]  # custom/static may not exist yet

# Admin uploads (the logo, FR-ADM-2) live in the data dir and are served by `il2ks.web.media`, not by WhiteNoise.
MEDIA_ROOT = DATA_DIR / "media"
MEDIA_URL = "/media/"
