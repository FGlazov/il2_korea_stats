"""The `SiteSettings` admin form: friendly editors for the accent colour, the links and the logo (FR-ADM-2, TD-25)."""

import re
from urllib.parse import urlsplit

from django import forms
from django.core.files.uploadedfile import UploadedFile
from django.utils.translation import gettext_lazy as _

from il2ks.db.models import SiteSettings
from il2ks.web.logo import MAX_UPLOAD_BYTES, LogoError, ProcessedLogo, process_logo

HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
MAX_LINKS = 10
MAX_LABEL = 60
MAX_URL = 300
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def parse_links(text: str) -> list[dict[str, str]]:
    """`Label | https://url` per line (blank lines ignored). Only absolute http(s) URLs. Raises `ValidationError`."""
    links: list[dict[str, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        label, sep, url = (part.strip() for part in line.rpartition("|"))
        if not sep or not label or not url:
            raise forms.ValidationError(
                _("Line %(n)d: write it as “Label | https://example.org”."), params={"n": number}, code="format"
            )
        parts = urlsplit(url)
        if parts.scheme.lower() not in {"http", "https"} or not parts.netloc or _CONTROL.search(url) or " " in url:
            raise forms.ValidationError(
                _("Line %(n)d: the link must be a full http:// or https:// address."), params={"n": number}, code="url"
            )
        if len(label) > MAX_LABEL or len(url) > MAX_URL:
            raise forms.ValidationError(_("Line %(n)d: the label or the address is too long."), params={"n": number})
        links.append({"label": label, "url": url})
    if len(links) > MAX_LINKS:
        raise forms.ValidationError(_("At most %(max)d links."), params={"max": MAX_LINKS}, code="too_many")
    return links


def format_links(links: list[dict[str, str]]) -> str:
    return "\n".join(f"{link.get('label', '')} | {link.get('url', '')}" for link in links)


class AccentColorInput(forms.TextInput):
    """A text box for `#RRGGBB` that gets a colour picker next to it (`accent_picker.js`); works without JS."""

    class Media:
        js = ("il2ks_admin/accent_picker.js",)

    def __init__(self) -> None:
        super().__init__(attrs={"placeholder": "#RRGGBB", "pattern": "#[0-9A-Fa-f]{6}", "size": 9, "maxlength": 7})


class SiteSettingsForm(forms.ModelForm):
    links_text = forms.CharField(
        label=_("Links"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 4, "cols": 70, "placeholder": "Discord | https://discord.gg/example"}),
        help_text=_("One per line: “Label | https://address”. Shown in the site header or footer. At most 10."),
    )
    logo_upload = forms.FileField(
        label=_("Upload a new logo"),
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg,image/webp"}),
        help_text=_("PNG, JPEG or WebP, up to 2 MB. The image is checked, re-encoded and scaled down; SVG is refused."),
    )
    remove_logo = forms.BooleanField(label=_("Remove the current logo"), required=False)

    class Meta:
        model = SiteSettings
        fields = ["site_title", "server_name", "description", "accent_color", "redfor_name", "blufor_name"]
        widgets = {"accent_color": AccentColorInput(), "description": forms.Textarea(attrs={"rows": 4, "cols": 70})}

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # pyright: ignore[reportArgumentType]
        self.processed_logo: ProcessedLogo | None = None
        if self.instance.pk is not None:
            self.fields["links_text"].initial = format_links(self.instance.links)

    def clean_accent_color(self) -> str:
        value: str = self.cleaned_data["accent_color"].strip()
        if value and not HEX_COLOR.fullmatch(value):
            raise forms.ValidationError(_("Use a colour like #1A73E8 (six hex digits), or leave it empty."))
        return value.upper()

    def clean_links_text(self) -> list[dict[str, str]]:
        return parse_links(self.cleaned_data["links_text"])

    def clean_logo_upload(self) -> UploadedFile | None:
        upload: UploadedFile | None = self.cleaned_data["logo_upload"]
        if upload is None:
            return None
        if upload.size > MAX_UPLOAD_BYTES:  # don't even read an oversized upload
            raise forms.ValidationError(_("The file is larger than 2 MB."))
        try:
            self.processed_logo = process_logo(upload.read(MAX_UPLOAD_BYTES + 1))
        except LogoError as exc:
            raise forms.ValidationError(str(exc)) from exc
        return upload

    def clean(self) -> dict[str, object]:
        cleaned = super().clean() or {}
        if cleaned.get("remove_logo") and self.processed_logo is not None:
            self.add_error("remove_logo", _("Either upload a new logo or remove the current one, not both."))
        return cleaned
