"""The first-run setup page `/setup/` (doc 07 option B): the browser way to finish what `il2ks setup` does.

Security is the design problem (an unauthenticated page that creates an admin account, on a server that may be public),
so the page answers 404 unless **all** of these hold (`il2ks.serving.setup_token` has the details):

1. setup is pending: `<data dir>/setup-token.txt` exists (`il2ks web` / `run` write it while no admin account exists)
   and still no admin account does;
2. the request came straight from this machine (loopback peer, no proxy headers, a `localhost` Host);
3. the URL carries the token (`?token=`), checked in constant time, wrong guesses counted and then locked out.

On submit it writes the config through the same code as the command (`ops.setup.complete_web_setup`), creates the admin,
deletes the token file and shows a summary with the doctor's findings. From then on the page is a 404.
"""

import os
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import render
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt, csrf_protect

from il2ks.config import CONFIG_FILE, Config, ConfigError, detect_os_timezone, load_config
from il2ks.ops import admin
from il2ks.ops.checks import raw_config
from il2ks.ops.detect import LogFolder
from il2ks.ops.doctor import Level, run_checks
from il2ks.ops.setup import complete_web_setup, default_log_finder
from il2ks.serving import procutil, setup_token
from il2ks.web.setup_forms import LogsStatus, SetupForm, zone_names

CANDIDATES_TTL_S = 120.0
"""The folder search takes up to a few seconds; a form shown again after a mistake reuses the last result."""

limiter = setup_token.AttemptLimiter()
_finish_lock = threading.Lock()
_candidates: tuple[float, list[LogFolder]] | None = None
_candidates_lock = threading.Lock()


@dataclass(frozen=True, slots=True)
class Candidate:
    path: str
    reports: int
    newest: str


def candidate_folders() -> list[LogFolder]:
    """Folders that look like DServer's text log folder (`ops.detect`), searched at most once per `CANDIDATES_TTL_S`."""
    global _candidates
    with _candidates_lock:
        if _candidates is None or time.monotonic() - _candidates[0] > CANDIDATES_TTL_S:
            found = default_log_finder(os.environ, Path.home(), sys.platform)()
            _candidates = (time.monotonic(), found)
        return _candidates[1]


def _is_pending(data_dir: Path) -> bool:
    """Setup is pending: a token file, and no admin account yet. An admin that appeared some other way (`il2ks
    createadmin`) ends it: the stale token is removed on the spot."""
    if not setup_token.read_token(data_dir):
        return False
    if admin.admin_exists():
        setup_token.discard_token(data_dir)
        return False
    return True


# `csrf_exempt` only switches off the global check, which would answer 403 before the gates below could answer 404; the
# form itself is checked by `csrf_protect` once the request has passed them.
@csrf_exempt
@never_cache
def setup(request: HttpRequest) -> HttpResponse:
    data_dir = Path(settings.DATA_DIR)
    if not setup_token.is_direct_local_request(request.META) or not _is_pending(data_dir):
        raise Http404
    if request.method not in {"GET", "HEAD", "POST"}:
        return HttpResponseNotAllowed(["GET", "POST"])
    if limiter.locked():
        return render(request, "il2ks/setup_gate.html", {"state": "locked", "minutes": 10}, status=429)
    given = (request.POST if request.method == "POST" else request.GET).get("token", "")
    if not given:
        return render(
            request,
            "il2ks/setup_gate.html",
            {"state": "need", "token_file": setup_token.token_path(data_dir)},
            status=403,
        )
    if not setup_token.token_matches(data_dir, given):
        limiter.record_failure()
        return render(
            request,
            "il2ks/setup_gate.html",
            {"state": "wrong", "token_file": setup_token.token_path(data_dir)},
            status=403,
        )
    limiter.reset()
    response = csrf_protect(_page)(request, data_dir, given)
    cookie = response.cookies.get(settings.CSRF_COOKIE_NAME)
    if cookie is not None and not request.is_secure():
        # Production marks the CSRF cookie Secure, but this page is reached over plain http://localhost (no HTTPS exists
        # yet) and not every browser sends Secure cookies there. The cookie is only for this page; the form's token
        # protects the submit as well.
        cookie["secure"] = ""
    return response


def _page(request: HttpRequest, data_dir: Path, token: str) -> HttpResponse:
    try:
        cfg = load_config(create_server_uid=False)
    except ConfigError as exc:
        return render(request, "il2ks/setup_gate.html", {"state": "broken", "problem": str(exc)}, status=500)
    zones = zone_names(cfg.timezone_name)
    if request.method == "POST":
        form = SetupForm(request.POST, zones=zones)
        if form.is_valid():
            done = _finish(request, cfg, form, data_dir)
            if done is not None:
                return done
    else:
        form = SetupForm(initial=_initial(cfg), zones=zones)
    return render(request, "il2ks/setup.html", _form_context(cfg, form, token, request.method == "POST"))


def _initial(cfg: Config) -> dict[str, object]:
    return {
        "timezone": cfg.timezone_name,
        "https_mode": cfg.https.mode,
        "domain": cfg.https.domain,
        "email": cfg.https.email,
        "logs_choice": str(cfg.logs.dir) if cfg.logs.dir is not None else "",
        "admin_username": admin.DEFAULT_USERNAME,
    }


def _configured_timezone(cfg: Config) -> bool:
    """Whether the admin (config file or environment) already chose the time zone; if not, it is a guess."""
    table = raw_config(cfg).get("server")
    return bool(os.environ.get("IL2KS_SERVER_TIMEZONE")) or (isinstance(table, dict) and "timezone" in table)


def _form_context(cfg: Config, form: SetupForm, token: str, submitted: bool) -> dict[str, object]:
    chosen = str(form["logs_choice"].value() or "")
    candidates = [
        Candidate(str(f.path), f.reports, datetime.fromtimestamp(f.newest_mtime).strftime("%Y-%m-%d %H:%M"))
        for f in candidate_folders()
    ]
    known = {c.path for c in candidates}
    if not submitted and not chosen and candidates:
        chosen = candidates[0].path  # the newest folder with reports, like the command's default
    elif chosen and chosen not in known and not str(form["logs_custom"].value() or ""):
        candidates.insert(0, Candidate(chosen, -1, ""))  # the folder the config already names
    return {
        "form": form,
        "token": token,
        "candidates": candidates,
        "chosen_logs": chosen,
        "logs_status": form.logs,
        "timezone_guessed": not _configured_timezone(cfg),
        "timezone_autofill": not submitted and not _configured_timezone(cfg),
        "windows": sys.platform == "win32",
        "detected_timezone": detect_os_timezone(os.environ),
        "config_exists": cfg.source is not None,
        "config_path": cfg.source or cfg.data_dir / CONFIG_FILE,
        "page_title": _("First-run setup"),
    }


# --- finishing ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FindingRow:
    tone: str
    label: str
    title: str
    detail: str
    fix: str


def _finding_rows(cfg: Config) -> list[FindingRow]:
    """The doctor's verdict on the new configuration, worst first. Our own web port is not a conflict."""
    try:
        findings = run_checks(cfg)
    except Exception:  # a broken check must not hide the summary of a setup that worked
        return []
    own_port = f"Port {cfg.web.port} (the web server)"
    shown = [f for f in findings if not f.title.startswith(own_port)]
    # Translators: setup check badges. "Fix" = a problem the admin must fix, "Check" = a warning worth a look.
    tones = {Level.ERROR: ("red", _("Fix")), Level.WARN: ("amber", _("Check")), Level.OK: ("green", _("OK"))}
    ordered = sorted(shown, key=lambda f: -f.level)
    return [FindingRow(*tones[f.level], f.title, f.detail, f.fix) for f in ordered]


def _finish(request: HttpRequest, cfg: Config, form: SetupForm, data_dir: Path) -> HttpResponse | None:
    """Write everything; the summary page, or None (with errors added to the form) when it could not be done.

    While this runs the marker `setup-finishing.txt` tells `il2ks run` not to restart the stack for the new
    configuration yet: the restart would cut off this very answer (`ConfigWatch` `hold`)."""
    setup_token.mark_finishing(data_dir)
    try:
        return _finish_marked(request, cfg, form, data_dir)
    finally:
        setup_token.clear_finishing(data_dir)


def _finish_marked(request: HttpRequest, cfg: Config, form: SetupForm, data_dir: Path) -> HttpResponse | None:
    answers = form.answers(cfg.data_dir)
    target = cfg.source or cfg.data_dir / CONFIG_FILE
    with _finish_lock:
        if not _is_pending(data_dir):  # a second submit that lost the race
            raise Http404
        try:
            result = complete_web_setup(
                answers,
                target,
                os.environ,
                admin_username=str(form.cleaned_data["admin_username"]),
                admin_password=str(form.cleaned_data["admin_password"]),
            )
        except admin.AdminError as exc:
            form.add_error("admin_password", str(exc))
            return None
        except (ConfigError, OSError) as exc:
            form.add_error(None, _("The configuration could not be saved: %(error)s") % {"error": exc})
            return None
        setup_token.discard_token(data_dir)
    stack = procutil.running_stack(result.applied.config.data_dir)
    logs: LogsStatus | None = form.logs
    new = result.applied.config
    context: dict[str, object] = {
        "page_title": _("Setup finished"),
        "result": result,
        "config_path": result.applied.target,
        "backup": result.applied.backup,
        "logs_status": logs,
        "findings": _finding_rows(new),
        "restarts_itself": stack is not None,
        "site_address": f"https://{new.https.domain}/" if new.https.domain and new.https.mode == "caddy" else "",
        "mode": new.https.mode,
        "domain": new.https.domain,
        "timezone": new.timezone_name,
        "username": str(form.cleaned_data["admin_username"]),
    }
    return render(request, "il2ks/setup_done.html", context)
