"""Field validators that guard values reaching public pages (branding, TD-25)."""

import re
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

_CONTROL = re.compile(r"[\x00-\x1f\x7f\s]")


def validate_http_url(value: str) -> None:
    """Only absolute `http://` / `https://` addresses with a host; no `javascript:`, `data:`, `mailto:`, relative paths,
    spaces or control characters. Used for the custom navigation links (opened in a new tab by the template)."""
    try:
        parts = urlsplit(value)
        host = parts.hostname
    except ValueError:  # e.g. an unbalanced bracket in an IPv6 host
        raise ValidationError(_("Enter a full http:// or https:// address."), code="url") from None
    if parts.scheme.lower() not in {"http", "https"} or not host or _CONTROL.search(value):
        raise ValidationError(_("Enter a full http:// or https:// address."), code="url")
