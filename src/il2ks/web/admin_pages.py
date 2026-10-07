"""The admin of Markdown pages (roadmap 0.2.0, OQ-133): a page's base text plus optional translations, each typed as
Markdown with a Preview button (the same renderer and sanitiser as the public page, `web.pages`).

A save renders every text once and stores the HTML (`publish_page`), refreshes the navigation links that point at the
page and bumps the data version (TD-28). Deleting a page removes the navigation links that pointed at it."""

from typing import TYPE_CHECKING

from django import forms
from django.contrib import admin, messages
from django.db import models
from django.forms import ModelForm
from django.forms.models import BaseModelFormSet
from django.http import HttpRequest, HttpResponse
from django.urls import URLPattern, path, reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString, mark_safe
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from il2ks.db.models import Page, PageTranslation, SiteSettings
from il2ks.db.site import bump_data_version
from il2ks.web.pages import (
    MAX_SOURCE_LENGTH,
    has_http_images,
    publish_nav_links,
    publish_page,
    render_markdown,
    site_languages,
)

if TYPE_CHECKING:
    from django.contrib.admin import ModelAdmin
else:
    # Same shim as `web.admin`: django-types makes `ModelAdmin[Model]` generic for the type checker only.
    class ModelAdmin[M: models.Model](admin.ModelAdmin):
        pass


HTTP_IMAGES_WARNING = _(
    "An image on this page has an http:// address. Pages only load images over https, so it will not load; "
    "use the https:// address of the image."
)

PREVIEW_URL_NAME = "admin:il2ks_db_page_preview"


class MarkdownWidget(forms.Textarea):
    """A textarea with a Preview button under it (the script is in `admin/il2ks_db/page/change_form.html`)."""

    def __init__(self) -> None:
        super().__init__(attrs={"rows": 14, "cols": 90, "class": "vLargeTextField", "data-markdown": "1"})

    def render(
        self,
        name: str,
        value: object,
        attrs: dict[str, object] | None = None,
        renderer: object = None,
    ) -> SafeString:
        area = super().render(name, value, attrs, renderer)  # pyright: ignore[reportArgumentType]
        return format_html(
            '{}<p><button type="button" class="md-preview-button" data-url="{}" data-failed="{}">{}</button></p>'
            '<div class="md-preview" hidden></div>',
            area,
            reverse(PREVIEW_URL_NAME),
            gettext("The preview could not be loaded."),
            gettext("Preview"),
        )


def _language_choices() -> list[tuple[str, str]]:
    return site_languages()


class PageForm(ModelForm):
    base_language = forms.ChoiceField(
        label=_("Language of the base text"),
        choices=_language_choices,
        initial="en",
        help_text=_(
            "The base text is shown to every visitor whose language has no translation below. English unless you "
            "write it in another language."
        ),
    )
    source = forms.CharField(
        label=_("Base text (Markdown)"),
        widget=MarkdownWidget(),
        max_length=MAX_SOURCE_LENGTH,
        required=False,
    )

    class Meta:
        model = Page
        fields = ("title", "slug", "base_language", "source")


class PageTranslationForm(ModelForm):
    language = forms.ChoiceField(label=_("Language"), choices=_language_choices)
    source = forms.CharField(
        label=_("Text (Markdown)"), widget=MarkdownWidget(), max_length=MAX_SOURCE_LENGTH, required=False
    )

    class Meta:
        model = PageTranslation
        fields = ("language", "title", "source")


class PageTranslationInline(admin.StackedInline):  # pyright: ignore[reportMissingTypeArgument]
    model = PageTranslation
    form = PageTranslationForm
    extra = 0
    max_num = len(_language_choices())
    verbose_name = _("translation")
    verbose_name_plural = _("Translations (optional)")


@admin.register(Page)
class PageAdmin(ModelAdmin[Page]):
    form = PageForm
    inlines = (PageTranslationInline,)
    list_display = ("title", "slug", "language_list", "updated_at")
    search_fields = ("title", "slug")
    prepopulated_fields = {"slug": ("title",)}  # noqa: RUF012
    readonly_fields = ("address",)
    fields = ("title", "slug", "address", "base_language", "source")
    change_form_template = "admin/il2ks_db/page/change_form.html"

    @admin.display(description=_("Translations"))
    def language_list(self, obj: Page) -> str:
        return ", ".join(sorted(obj.translations)) or "-"

    @admin.display(description=_("Address"))
    def address(self, obj: Page | None) -> str:
        if obj is None or not obj.pk:
            return gettext("The address is /p/ and the short name, once saved. Link to it from the navigation links.")
        return format_html('<a href="{0}">{0}</a>', reverse("web:page", args=[obj.slug]))

    def get_urls(self) -> list[URLPattern]:
        preview = path("preview/", self.admin_site.admin_view(self.preview_view), name="il2ks_db_page_preview")
        return [preview, *super().get_urls()]

    def preview_view(self, request: HttpRequest) -> HttpResponse:
        """POST `source`: the sanitised HTML the public page would show (nothing is saved)."""
        if request.method != "POST" or not (self.has_add_permission(request) or self.has_change_permission(request)):
            return HttpResponse(status=405)
        html = render_markdown(str(request.POST.get("source", "")))
        if has_http_images(html):
            html = format_html('<p class="md-warning" role="alert">{}</p>{}', HTTP_IMAGES_WARNING, mark_safe(html))
        response = HttpResponse(html)
        response["Content-Security-Policy"] = "default-src 'none'; img-src https: data:"
        return response

    def save_related(self, request: HttpRequest, form: ModelForm, formsets: BaseModelFormSet, change: bool) -> None:
        super().save_related(request, form, formsets, change)
        instance = form.instance
        assert isinstance(instance, Page)
        publish_page(instance)
        bump_data_version()
        texts = [instance.html, *(row.html for row in PageTranslation.objects.filter(page=instance))]
        if any(has_http_images(text) for text in texts):
            self.message_user(request, HTTP_IMAGES_WARNING, messages.WARNING)

    def delete_model(self, request: HttpRequest, obj: Page) -> None:
        sites = list(SiteSettings.objects.filter(nav_links__page=obj).distinct())
        super().delete_model(request, obj)
        self._after_delete(sites)

    def delete_queryset(self, request: HttpRequest, queryset: models.QuerySet[Page]) -> None:
        sites = list(SiteSettings.objects.filter(nav_links__page__in=queryset).distinct())
        super().delete_queryset(request, queryset)
        self._after_delete(sites)

    @staticmethod
    def _after_delete(sites: list[SiteSettings]) -> None:
        for site in sites:
            publish_nav_links(site)
        bump_data_version()
