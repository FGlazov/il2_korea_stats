"""The form of "Reprocess a time span" (FR-ADM-4, doc 14): dates, and if wanted times, of the server's local clock."""

from datetime import date, datetime, time
from typing import Any

from django import forms
from django.utils.translation import gettext_lazy as _

from il2ks.core.logparse.files import SpanBound, span_is_reversed


def _date_field(label: str) -> forms.DateField:
    widget = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    return forms.DateField(label=label, required=False, widget=widget)


def _time_field(label: str) -> forms.TimeField:
    widget = forms.TimeInput(attrs={"type": "time"}, format="%H:%M")
    return forms.TimeField(label=label, required=False, widget=widget)


def _bound(day: date | None, moment: time | None) -> SpanBound | None:
    return datetime.combine(day, moment) if day is not None and moment is not None else day


class ReprocessSpanForm(forms.Form):
    since_date = _date_field(_("From date"))
    since_time = _time_field(_("From time (optional)"))
    until_date = _date_field(_("To date"))
    until_time = _time_field(_("To time (optional)"))

    def span(self) -> tuple[SpanBound | None, SpanBound | None]:
        """The two ends of a valid form; a date without a time is the whole day."""
        data = self.cleaned_data
        return _bound(data["since_date"], data["since_time"]), _bound(data["until_date"], data["until_time"])

    def clean(self) -> dict[str, Any]:
        data = super().clean()
        if self.errors:
            return data
        for day, moment in (("since_date", "since_time"), ("until_date", "until_time")):
            if data[moment] is not None and data[day] is None:
                self.add_error(day, _("A time needs a date."))
        if self.errors:
            return data
        if data["since_date"] is None and data["until_date"] is None:
            raise forms.ValidationError(_("Enter a start date, an end date or both."))
        if span_is_reversed(*self.span()):
            raise forms.ValidationError(_("The start is after the end."))
        return data
