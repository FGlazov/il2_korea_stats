"""The `SiteSettings` admin form (colour editor, fonts, logo) and the navigation-link formset (FR-ADM-2, TD-25)."""

from collections.abc import Mapping
from typing import cast

from django import forms
from django.core.files.uploadedfile import UploadedFile
from django.template.loader import render_to_string
from django.utils.safestring import SafeString
from django.utils.translation import gettext_lazy as _

from il2ks.db.models import SiteSettings
from il2ks.web.fonts import (
    MAX_CUSTOM_FONTS,
    MAX_FONT_BYTES,
    CustomFont,
    FontError,
    ProcessedFont,
    clean_fonts,
    process_font,
)
from il2ks.web.logo import MAX_UPLOAD_BYTES, LogoError, ProcessedLogo, process_logo
from il2ks.web.theme import (
    BODY_FONTS,
    GROUPS,
    HEADING_FONTS,
    HEX_COLOR,
    MODES,
    PRESETS,
    TOKENS,
    Mode,
    StrOrPromise,
    Theme,
    clean_theme,
)

MAX_NAV_LINKS = 30
# From the layout measurements in tests/e2e/test_nav_layout.py: up to this many extra links with short labels share the
# header row with the built-in links at 1280 px and wider; more wrap onto a second row (nothing breaks).
RECOMMENDED_NAV_LINKS = 3


def _field_name(prefix: str, key: str, mode: str) -> str:
    return f"{prefix}__{key}__{mode}"


def _boxes(value: object, mode: Mode) -> Mapping[str, object]:
    """The `{token: text}` of one mode from a submitted or stored theme (anything else = no boxes)."""
    if not isinstance(value, Mapping):
        return {}
    boxes: object = value.get(mode)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return boxes if isinstance(boxes, Mapping) else {}  # pyright: ignore[reportUnknownVariableType]


class ThemeWidget(forms.Widget):
    """A table of colour inputs: one row per token, a light and a dark column (`theme_editor.js` adds pickers)."""

    class Media:
        css = {"all": ("il2ks_admin/theme_editor.css",)}
        js = ("il2ks_admin/theme_editor.js",)

    def value_from_datadict(self, data: Mapping[str, object], files: object, name: str) -> Theme:
        raw = {
            mode: {t.key: str(data.get(_field_name(name, t.key, mode), "")).strip() for t in TOKENS} for mode in MODES
        }
        return {"light": raw["light"], "dark": raw["dark"]}

    def render(
        self, name: str, value: object, attrs: Mapping[str, object] | None = None, renderer: object = None
    ) -> SafeString:
        groups: list[dict[str, object]] = []
        for group_key, group_label in GROUPS:
            rows: list[dict[str, object]] = []
            for token in (t for t in TOKENS if t.group == group_key):
                row: dict[str, object] = {"label": token.label, "key": token.key}
                for mode in MODES:
                    default = token.light if mode == "light" else token.dark
                    row[mode] = {
                        "name": _field_name(name, token.key, mode),
                        "value": str(_boxes(value, mode).get(token.key, "")),
                        "default": (default or "").upper(),
                    }
                rows.append(row)
            groups.append({"label": group_label, "rows": rows})
        return SafeString(render_to_string("il2ks_admin/theme_editor.html", {"groups": groups}))


class ThemeField(forms.Field):
    """The colour overrides: every non-empty box must be exactly `#RRGGBB`. Empty = the default colour."""

    widget = ThemeWidget

    def to_python(self, value: object) -> Theme:
        theme: Theme = {"light": {}, "dark": {}}
        errors: list[forms.ValidationError] = []
        for mode in MODES:
            boxes = _boxes(value, mode)
            for token in TOKENS:
                text = str(boxes.get(token.key, "")).strip()
                if not text:
                    continue
                if not HEX_COLOR.fullmatch(text):
                    errors.append(
                        forms.ValidationError(
                            _("%(token)s (%(mode)s): use a color like #1A73E8 (six hex digits), or leave it empty."),
                            params={"token": token.label, "mode": _("light") if mode == "light" else _("dark")},
                            code="color",
                        )
                    )
                else:
                    theme[mode][token.key] = text.upper()
        if errors:
            raise forms.ValidationError(list(errors))
        return theme


def _choices(fonts: Mapping[str, tuple[StrOrPromise, str]]) -> list[tuple[str, str]]:
    """(key, label) pairs; the labels stay lazy, so they are translated when the form is shown."""
    return [(key, cast("str", label)) for key, (label, _stack) in fonts.items()]


class SiteSettingsForm(forms.ModelForm):
    theme = ThemeField(
        label=_("Colors"),
        required=False,
        help_text=_(
            "Leave a box empty to keep the default color. “Light” and “dark” are the two modes of the theme toggle. "
            "“Automatic” colors follow the accent."
        ),
    )
    theme_preset = forms.ChoiceField(
        label=_("Start from a color scheme"),
        required=False,
        choices=[("", _("— keep the colors below —")), ("default", _("Default (clears all colors below)"))]
        + [(key, label) for key, (label, _theme) in PRESETS.items()],
        help_text=_("Replaces the colors below with that scheme when you save. You can then adjust single colors."),
    )
    heading_font = forms.ChoiceField(label=_("Heading font"), required=False, choices=_choices(HEADING_FONTS))
    body_font = forms.ChoiceField(
        label=_("Body font"),
        required=False,
        choices=_choices(BODY_FONTS),
        help_text=_(
            "Fonts come from the visitor's system or are shipped with il2ks; nothing is loaded from other websites."
        ),
    )
    logo_upload = forms.FileField(
        label=_("Upload a new logo"),
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg,image/webp"}),
        help_text=_("PNG, JPEG or WebP, up to 2 MB. The image is checked, re-encoded and scaled down; SVG is refused."),
    )
    remove_logo = forms.BooleanField(label=_("Remove the current logo"), required=False)
    font_upload = forms.FileField(
        label=_("Upload a font"),
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": ".woff2,.woff,font/woff2,font/woff"}),
        help_text=_(
            "A .woff2 (preferred) or .woff file, up to 2 MB; smaller is faster, so a file under 100 KB is ideal. "
            "Make sure the font's license allows web use. It is stored on your server and served from your own site; "
            "nothing is loaded from other websites."
        ),
    )
    font_use = forms.ChoiceField(
        label=_("Use the uploaded font for"),
        required=False,
        choices=[
            ("", _("— nothing yet (choose it in the lists below) —")),
            ("heading", _("Headings")),
            ("body", _("Body text")),
            ("both", _("Headings and body text")),
        ],
    )
    remove_fonts = forms.MultipleChoiceField(
        label=_("Remove uploaded fonts"), required=False, widget=forms.CheckboxSelectMultiple, choices=[]
    )

    class Meta:
        model = SiteSettings
        fields = [
            "site_title",
            "server_name",
            "description",
            "theme",
            "heading_font",
            "body_font",
            "redfor_name",
            "blufor_name",
            "redfor_emblem",
            "blufor_emblem",
        ]
        widgets = {"description": forms.Textarea(attrs={"rows": 4, "cols": 70})}

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # pyright: ignore[reportArgumentType]
        self.processed_logo: ProcessedLogo | None = None
        self.processed_font: ProcessedFont | None = None
        self.stored_fonts: list[CustomFont] = clean_fonts(self.instance.custom_fonts)
        if self.instance.pk is not None:
            self.initial["theme"] = clean_theme(self.instance.theme)
        # Translators: a short suffix in the heading/body font drop-down, after the font's name: "Fira Sans (uploaded)"
        # marks a font the admin uploaded, as opposed to the built-in choices.
        uploaded = [(font.key, f"{font.label} ({_('uploaded')})") for font in self.stored_fonts]
        for name, builtin in (("heading_font", HEADING_FONTS), ("body_font", BODY_FONTS)):
            self.fields[name].choices = _choices(builtin) + uploaded
        self.fields["remove_fonts"].choices = [(font.key, font.label) for font in self.stored_fonts]

    @property
    def kept_fonts(self) -> list[CustomFont]:
        """The uploaded fonts that stay after this save, plus the new upload (valid after `is_valid`)."""
        removed = set(self.cleaned_data.get("remove_fonts") or [])
        fonts = [font for font in self.stored_fonts if font.key not in removed]
        new = self.processed_font.font if self.processed_font is not None else None
        if new is not None and all(font.file != new.file for font in fonts):
            fonts.append(new)
        return fonts

    def clean_font_upload(self) -> UploadedFile | None:
        upload: UploadedFile | None = self.cleaned_data["font_upload"]
        if upload is None:
            return None
        if upload.size > MAX_FONT_BYTES:  # don't even read an oversized upload
            raise forms.ValidationError(_("The file is larger than 2 MB."))
        try:
            self.processed_font = process_font(upload.read(MAX_FONT_BYTES + 1), upload.name or "")
        except FontError as exc:
            raise forms.ValidationError(str(exc)) from exc
        return upload

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
        self._clean_fonts(cleaned)
        preset = cleaned.get("theme_preset")
        if preset == "default":
            cleaned["theme"] = {"light": {}, "dark": {}}
        elif isinstance(preset, str) and preset in PRESETS:
            cleaned["theme"] = clean_theme(PRESETS[preset][1])
        return cleaned

    def _clean_fonts(self, cleaned: dict[str, object]) -> None:
        """Font selection: a removed font stops being used, a new upload can be selected right away, and the list is
        capped."""
        if self.errors.get("font_upload") or self.errors.get("remove_fonts"):
            return
        removed = set(cast("list[str]", cleaned.get("remove_fonts") or []))
        for name in ("heading_font", "body_font"):
            if cleaned.get(name) in removed:
                cleaned[name] = ""
        kept = self.kept_fonts
        if len(kept) > MAX_CUSTOM_FONTS:
            self.add_error(
                "font_upload",
                _("At most %(max)d uploaded fonts: tick one under “Remove uploaded fonts” first.")
                % {"max": MAX_CUSTOM_FONTS},
            )
            return
        if self.processed_font is not None:
            key = self.processed_font.font.key
            use = cleaned.get("font_use")
            if use in {"heading", "both"}:
                cleaned["heading_font"] = key
            if use in {"body", "both"}:
                cleaned["body_font"] = key


class NavLinkFormSet(forms.BaseInlineFormSet):
    """The ordered list of extra navigation links; at most `MAX_NAV_LINKS`."""

    def clean(self) -> None:
        super().clean()
        if any(self.errors):
            return
        live = [f for f in self.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]
        if len(live) > MAX_NAV_LINKS:
            raise forms.ValidationError(
                _("At most %(max)d navigation links."), params={"max": MAX_NAV_LINKS}, code="too_many"
            )
