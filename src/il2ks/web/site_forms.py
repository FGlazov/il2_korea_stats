"""The `SiteSettings` admin form (colour editor, fonts, logo) and the navigation-link formset (FR-ADM-2, TD-25)."""

from collections.abc import Mapping
from typing import cast

from django import forms
from django.core.files.uploadedfile import UploadedFile
from django.template.loader import render_to_string
from django.utils.safestring import SafeString
from django.utils.translation import gettext_lazy as _

from il2ks.db.models import HomeFeature, SiteSettings
from il2ks.web.branding_images import (
    MAX_BACKGROUND_BYTES,
    BackgroundKind,
    ProcessedBackground,
    ProcessedIcons,
    process_background,
    process_icons,
)
from il2ks.web.feature_image import (
    FeatureImageError,
    ProcessedFeature,
    Source,
    inspect_source,
    process_feature,
)
from il2ks.web.fonts import (
    MAX_CUSTOM_FONTS,
    MAX_FONT_BYTES,
    CustomFont,
    FontError,
    ProcessedFont,
    clean_fonts,
    process_font,
)
from il2ks.web.logo import MAX_PIXELS, MAX_UPLOAD_BYTES, LogoError, ProcessedLogo, process_logo
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


FOCUS_HELP = _("The part of the picture that stays visible when it is cropped to a narrow screen.")
SHADE_HELP = _("Lays the band color over the picture so the text stays readable. Bright pictures need 60 to 80 %.")


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
    favicon_upload = forms.FileField(
        label=_("Icon for the browser tab"),
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg,image/webp"}),
        help_text=_(
            "Optional. Without it the logo is used as the tab icon (padded to a square, never cropped); with neither, "
            "the built-in aircraft icon stays. PNG, JPEG or WebP, up to 2 MB; a square image of at least 180 × 180 "  # noqa: RUF001
            "pixels works best."
        ),
    )
    remove_favicon = forms.BooleanField(label=_("Remove the separate tab icon"), required=False)
    header_bg_upload = forms.FileField(
        label=_("Upload a header background"),
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg,image/webp"}),
        help_text=_(
            "A picture behind the top banner on every page. PNG, JPEG or WebP, up to 4 MB; about 1920 × 400 pixels "  # noqa: RUF001
            "is a good size. It is cropped to fit at every screen width, so keep the important part where the "
            "focus below points. The image is checked and re-encoded; SVG is refused."
        ),
    )
    remove_header_bg = forms.BooleanField(label=_("Remove it and use the built-in decoration"), required=False)
    home_bg_upload = forms.FileField(
        label=_("Upload a home banner background"),
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg,image/webp"}),
        help_text=_(
            "A picture behind the title and player search on the home page. PNG, JPEG or WebP, up to 4 MB; about "
            "1600 × 500 pixels is a good size. It is cropped to fit at every screen width. The image is checked and "  # noqa: RUF001
            "re-encoded; SVG is refused."
        ),
    )
    remove_home_bg = forms.BooleanField(label=_("Remove it and use the built-in decoration"), required=False)
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
            "home_feature",
            "feature_image_path",
            "feature_caption",
            "feature_alt",
            "home_bg_position",
            "home_bg_shade",
            "header_bg_position",
            "header_bg_shade",
            "redfor_name",
            "blufor_name",
            "redfor_emblem",
            "blufor_emblem",
            "show_live_sorties",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4, "cols": 70}),
            "feature_image_path": forms.TextInput(attrs={"size": 70}),
            "feature_caption": forms.TextInput(attrs={"size": 70}),
            "feature_alt": forms.TextInput(attrs={"size": 70}),
        }
        labels = {
            "home_feature": _("Large image on the front page"),
            "feature_image_path": _("Image file on the server"),
            "feature_caption": _("Caption"),
            "feature_alt": _("Alternative text"),
            "home_bg_position": _("Focus of the picture"),
            "header_bg_position": _("Focus of the picture"),
            "home_bg_shade": _("Darkening (0 to 80 %)"),
            "header_bg_shade": _("Darkening (0 to 80 %)"),
        }
        help_texts = {
            "home_feature": _("Off by default: the front page then looks as usual."),
            "feature_image_path": _(
                "Full path of a PNG, JPEG or WebP file on the machine that runs il2ks, for example a map that "
                "another program regenerates. il2ks checks the file every few seconds and shows the new picture "
                "within about ten seconds. The file itself is never published: a checked, re-encoded copy is "
                "(at most 2560 px wide, 40 MB and 64 megapixels). Docker: the file must be mounted into the "
                "container. Windows service: the account NT SERVICE\\il2ks needs read access."
            ),
            "home_bg_position": FOCUS_HELP,
            "header_bg_position": FOCUS_HELP,
            "home_bg_shade": SHADE_HELP,
            "header_bg_shade": SHADE_HELP,
            "feature_caption": _("Optional title shown under the image."),
            "feature_alt": _(
                "Required when the image is on: a short description for people who cannot see the picture."
            ),
        }

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # pyright: ignore[reportArgumentType]
        self.processed_logo: ProcessedLogo | None = None
        self.logo_icons: ProcessedIcons | None = None  # the tab icon made from a newly uploaded logo
        self.processed_favicon: ProcessedIcons | None = None
        self.processed_backgrounds: dict[BackgroundKind, ProcessedBackground] = {}
        for name in ("home_bg_position", "home_bg_shade", "header_bg_position", "header_bg_shade"):
            self.fields[name].required = False  # a post without them keeps the current choice
        self.fields["home_feature"].required = False  # a post without it keeps the current choice
        self.processed_font: ProcessedFont | None = None
        self.processed_feature: tuple[Source, ProcessedFeature] | None = None
        self.processed_feature: tuple[Source, ProcessedFeature] | None = None
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

    def clean_home_feature(self) -> str:
        return str(self.cleaned_data.get("home_feature") or self.instance.home_feature or HomeFeature.NONE.value)

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
            raw = upload.read(MAX_UPLOAD_BYTES + 1)
            self.processed_logo = process_logo(raw)
            self.logo_icons = process_icons(raw, max_bytes=MAX_UPLOAD_BYTES, max_pixels=MAX_PIXELS)
        except LogoError as exc:
            raise forms.ValidationError(str(exc)) from exc
        return upload

    def clean_favicon_upload(self) -> UploadedFile | None:
        upload: UploadedFile | None = self.cleaned_data["favicon_upload"]
        if upload is None:
            return None
        if upload.size > MAX_UPLOAD_BYTES:
            raise forms.ValidationError(_("The file is larger than 2 MB."))
        try:
            self.processed_favicon = process_icons(
                upload.read(MAX_UPLOAD_BYTES + 1), max_bytes=MAX_UPLOAD_BYTES, max_pixels=MAX_PIXELS
            )
        except LogoError as exc:
            raise forms.ValidationError(str(exc)) from exc
        return upload

    def _background(self, kind: BackgroundKind) -> UploadedFile | None:
        upload: UploadedFile | None = self.cleaned_data[f"{kind}_bg_upload"]
        if upload is None:
            return None
        if upload.size > MAX_BACKGROUND_BYTES:
            raise forms.ValidationError(_("The file is larger than 4 MB."))
        try:
            self.processed_backgrounds[kind] = process_background(upload.read(MAX_BACKGROUND_BYTES + 1), kind)
        except LogoError as exc:
            raise forms.ValidationError(str(exc)) from exc
        return upload

    def clean_home_bg_upload(self) -> UploadedFile | None:
        return self._background("home")

    def clean_header_bg_upload(self) -> UploadedFile | None:
        return self._background("header")

    def _clean_position_and_shade(self, kind: BackgroundKind) -> None:
        """A post that omits the focus or the darkening keeps what is stored (a missing value is not an error)."""
        if not self.cleaned_data.get(f"{kind}_bg_position") and f"{kind}_bg_position" not in self.errors:
            self.cleaned_data[f"{kind}_bg_position"] = getattr(self.instance, f"{kind}_bg_position")
        if self.cleaned_data.get(f"{kind}_bg_shade") is None and f"{kind}_bg_shade" not in self.errors:
            self.cleaned_data[f"{kind}_bg_shade"] = getattr(self.instance, f"{kind}_bg_shade")

    def clean(self) -> dict[str, object]:
        cleaned = super().clean() or {}
        if cleaned.get("remove_logo") and self.processed_logo is not None:
            self.add_error("remove_logo", _("Either upload a new logo or remove the current one, not both."))
        for flag, upload in (
            ("remove_favicon", self.processed_favicon),
            ("remove_home_bg", self.processed_backgrounds.get("home")),
            ("remove_header_bg", self.processed_backgrounds.get("header")),
        ):
            if cleaned.get(flag) and upload is not None:
                self.add_error(flag, _("Either upload a new file or remove the current one, not both."))
        self._clean_position_and_shade("home")
        self._clean_position_and_shade("header")
        self._clean_fonts(cleaned)
        self._clean_feature(cleaned)
        preset = cleaned.get("theme_preset")
        if preset == "default":
            cleaned["theme"] = {"light": {}, "dark": {}}
        elif isinstance(preset, str) and preset in PRESETS:
            cleaned["theme"] = clean_theme(PRESETS[preset][1])
        return cleaned

    def _clean_feature(self, cleaned: dict[str, object]) -> None:
        """Front-page image: a path and alt text are required when it is on, and the file is checked (and re-encoded)
        when something changed, so a bad path is refused here with a clear message instead of being saved."""
        if cleaned.get("home_feature") != HomeFeature.IMAGE:
            return
        path = str(cleaned.get("feature_image_path") or "").strip()
        if not path:
            self.add_error("feature_image_path", _("Enter the location of the image file."))
        if not str(cleaned.get("feature_alt") or "").strip():
            self.add_error("feature_alt", _("Describe the image in a few words; screen readers read this text."))
        if not path:
            return
        row = self.instance
        unchanged = (
            row.home_feature == HomeFeature.IMAGE
            and row.feature_image_path.strip() == path
            and bool(row.feature_image)
            and not row.feature_error
        )
        if unchanged:
            return  # `web.feature_image.sync` follows later changes of the file itself
        try:
            source = inspect_source(path)
            self.processed_feature = (source, process_feature(source))
        except FeatureImageError as exc:
            self.add_error("feature_image_path", str(exc))

    def _clean_feature(self, cleaned: dict[str, object]) -> None:
        """Front-page image: a path and alt text are required when it is on, and the file is checked (and re-encoded)
        when something changed, so a bad path is refused here with a clear message instead of being saved."""
        if cleaned.get("home_feature") != HomeFeature.IMAGE:
            return
        path = str(cleaned.get("feature_image_path") or "").strip()
        if not path:
            self.add_error("feature_image_path", _("Enter the location of the image file."))
        if not str(cleaned.get("feature_alt") or "").strip():
            self.add_error("feature_alt", _("Describe the image in a few words; screen readers read this text."))
        if not path:
            return
        row = self.instance
        unchanged = (
            row.home_feature == HomeFeature.IMAGE
            and row.feature_image_path.strip() == path
            and bool(row.feature_image)
            and not row.feature_error
        )
        if unchanged:
            return  # `web.feature_image.sync` follows later changes of the file itself
        try:
            source = inspect_source(path)
            self.processed_feature = (source, process_feature(source))
        except FeatureImageError as exc:
            self.add_error("feature_image_path", str(exc))

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
