"""The first-run setup form (doc 07 option B): the same answers `il2ks setup` asks, checked the same way.

Validation reuses the CLI's rules (`ops.setup.normalize_domain`, `valid_timezone`, Django's password validators through
`ops.admin`), so the page and the command accept and reject the same input.
"""

import zoneinfo
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from il2ks.ops import admin
from il2ks.ops.detect import LogFolder, inspect_folder
from il2ks.ops.setup import DEFAULT_HTTPS_MODE, HTTPS_MODES, SetupAnswers, normalize_domain

_HIDDEN_ZONE_PREFIXES = ("posix/", "right/")
_HIDDEN_ZONES = frozenset({"Factory", "localtime", "posixrules"})


@dataclass(frozen=True, slots=True)
class LogsStatus:
    """What the chosen log folder looks like, for the page to say in words."""

    path: Path
    exists: bool
    reports: int  # mission report files directly inside (0 = none yet)
    newest_mtime: float


def zone_names(current: str) -> list[str]:
    """IANA zone names for the select: `current` first, then the rest alphabetically."""
    names = sorted(
        name
        for name in zoneinfo.available_timezones()
        if not name.startswith(_HIDDEN_ZONE_PREFIXES) and name not in _HIDDEN_ZONES
    )
    if current not in names:
        names.append(current)
    return [current, *(name for name in names if name != current)]


def resolve_logs_folder(choice: str, custom: str, *, allow_missing: bool) -> LogsStatus | None:
    """The folder the admin picked: a typed path wins over the selected candidate; neither means "skip for now".

    Raises `ValidationError` for a path that is a file, or that does not exist unless `allow_missing` (like the CLI's
    "use it anyway")."""
    text = (custom.strip() or choice.strip()).strip('"')
    if not text:
        return None
    path = Path(text).expanduser().resolve()
    if path.is_file():
        raise ValidationError(_("%(path)s is a file, not a folder."), params={"path": path}, code="not_a_folder")
    if not path.is_dir():
        if not allow_missing:
            raise ValidationError(
                _(
                    "The folder %(path)s does not exist. Check the spelling, or tick “use it anyway” if it will "
                    "appear later (for example a network share that is not connected yet)."
                ),
                params={"path": path},
                code="missing",
            )
        return LogsStatus(path, exists=False, reports=0, newest_mtime=0.0)
    found: LogFolder | None = inspect_folder(path)
    return LogsStatus(
        path, exists=True, reports=found.reports if found else 0, newest_mtime=found.newest_mtime if found else 0
    )


class SetupForm(forms.Form):
    logs_choice = forms.CharField(required=False)  # a candidate folder's path (radio buttons)
    logs_custom = forms.CharField(label=_("Or type the folder"), required=False, max_length=1000)
    logs_allow_missing = forms.BooleanField(label=_("Use it anyway if it does not exist yet"), required=False)
    timezone = forms.ChoiceField(label=_("Time zone of the game server's computer"))
    https_mode = forms.ChoiceField(
        label=_("How should HTTPS be handled?"),
        choices=[(mode, mode) for mode in HTTPS_MODES],
        widget=forms.RadioSelect,
        initial=DEFAULT_HTTPS_MODE,
    )
    domain = forms.CharField(label=_("Domain name"), required=False, max_length=253)
    email = forms.EmailField(label=_("E-mail for certificate notices (optional)"), required=False)
    admin_username = forms.CharField(label=_("Admin user name"), max_length=150, initial=admin.DEFAULT_USERNAME)
    admin_password = forms.CharField(
        label=_("Password"), widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"})
    )
    admin_password2 = forms.CharField(
        label=_("Type the password again"),
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
    )

    def __init__(self, *args: object, zones: list[str], **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # pyright: ignore[reportArgumentType]
        timezone = cast(forms.ChoiceField, self.fields["timezone"])
        timezone.choices = [(name, name) for name in zones]
        self.logs: LogsStatus | None = None
        self.normalized_domain = ""

    def clean_domain(self) -> str:
        try:
            self.normalized_domain = normalize_domain(cast(str, self.cleaned_data["domain"]))
        except ValueError as exc:
            raise ValidationError(str(exc), code="domain") from exc
        return self.normalized_domain

    def clean_admin_username(self) -> str:
        username = cast(str, self.cleaned_data["admin_username"]).strip()
        field = get_user_model()._meta.get_field("username")
        field.run_validators(username)
        return username

    def clean(self) -> dict[str, object]:
        data = super().clean() or {}
        try:
            self.logs = resolve_logs_folder(
                cast(str, data.get("logs_choice", "")),
                cast(str, data.get("logs_custom", "")),
                allow_missing=bool(data.get("logs_allow_missing")),
            )
        except ValidationError as exc:
            self.add_error("logs_custom", exc)
        password = cast(str, data.get("admin_password", ""))
        username = cast(str, data.get("admin_username", ""))
        if password and data.get("admin_password2") != password:
            self.add_error("admin_password2", _("The two passwords differ."))
        elif password and username:
            for problem in admin.password_problems(password, username):
                self.add_error("admin_password", problem)
        return data

    def answers(self, data_dir: Path) -> SetupAnswers:
        """The checked answers as the setup code wants them (call only after `is_valid()`)."""
        cleaned = self.cleaned_data
        return SetupAnswers(
            data_dir=data_dir,
            timezone=cast(str, cleaned["timezone"]),
            https_mode=next(mode for mode in HTTPS_MODES if mode == cleaned["https_mode"]),
            domain=self.normalized_domain,
            email=cast(str, cleaned["email"]).strip(),
            logs_dir=self.logs.path if self.logs is not None else None,
        )
