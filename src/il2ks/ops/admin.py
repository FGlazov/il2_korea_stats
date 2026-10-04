"""The admin account (FR-ADM-1, `il2ks createadmin`): create it or reset its password, with Django's password checks.

Needs Django to be set up and the database migrated.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from il2ks.ops.prompt import Prompter

PASSWORD_ENV = "IL2KS_ADMIN_PASSWORD"
DEFAULT_USERNAME = "admin"
MAX_PASSWORD_TRIES = 3


class AdminError(ValueError):
    """The account could not be created; the message says what to change."""


def password_problems(password: str, username: str, email: str = "") -> list[str]:
    """What Django's validators dislike about `password` (empty list = fine).

    Uses `AUTH_PASSWORD_VALIDATORS` from the settings (`il2ks.settings` lists Django's four standard ones), the same
    rules the admin's own password forms apply."""
    from django.contrib.auth import get_user_model
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError

    user = get_user_model()(username=username, email=email)
    try:
        validate_password(password, user)
    except ValidationError as exc:
        return [str(message) for message in exc.messages]
    return []


def read_password(env: Mapping[str, str], password_file: Path | None) -> str | None:
    """The password for a script: from `--password-file` (only the line break at the end is dropped, a UTF-8 BOM is
    ignored), else the `IL2KS_ADMIN_PASSWORD` environment variable, else None."""
    if password_file is not None:
        try:
            text = password_file.read_text(encoding="utf-8-sig")  # the Windows installer's writer may add a BOM
        except OSError as exc:
            raise AdminError(f"cannot read the password file: {exc}") from exc
        password = text.rstrip("\r\n")
        if not password:
            raise AdminError(f"the password file {password_file} is empty")
        return password
    return env.get(PASSWORD_ENV) or None


def prompt_password(io: Prompter, username: str, email: str = "") -> str:
    """Ask twice (not echoed) and check it; up to three attempts."""
    for _ in range(MAX_PASSWORD_TRIES):
        first = io.ask_secret("Password for the admin account")
        if first != io.ask_secret("Type the password again"):
            io.say("The two passwords differ. Try again.")
            continue
        problems = password_problems(first, username, email)
        if problems:
            io.say("That password is not good enough:")
            for problem in problems:
                io.say(f"  - {problem}")
            continue
        return first
    raise AdminError("no acceptable password after three tries")


def admin_exists() -> bool:
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(is_superuser=True).exists()


def user_exists(username: str) -> bool:
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(username=username).exists()


def save_admin(username: str, password: str, email: str = "") -> Literal["created", "reset"]:
    """Create the superuser, or make an existing account of that name a working admin with this password.

    The password is checked here as well, so no caller can skip the check. Raises `AdminError` if it fails."""
    from django.contrib.auth import get_user_model

    if not username.strip():
        raise AdminError("the user name is empty")
    problems = password_problems(password, username, email)
    if problems:
        raise AdminError("password rejected: " + " ".join(problems))
    users = get_user_model().objects
    user = users.filter(username=username).first()
    outcome: Literal["created", "reset"] = "reset"
    if user is None:
        user = get_user_model()(username=username)
        outcome = "created"
    if email:
        user.email = email
    user.is_staff = True
    user.is_superuser = True
    user.is_active = True
    user.set_password(password)
    user.save()
    return outcome
