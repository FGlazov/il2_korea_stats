"""Doctor check for the admin login lockout (NFR-SEC-8): which accounts and addresses are locked right now."""

from collections.abc import Iterable

from django.db import DatabaseError

from il2ks.config import Config
from il2ks.ops.doctor import Finding, Level, check


@check
def login_lockout_check(cfg: Config) -> Iterable[Finding]:
    from il2ks.web.login_protection import current_lockouts

    title = "Admin login protection"
    attempts, minutes = cfg.web.login_attempts, cfg.web.login_lockout_minutes
    rule = f"{attempts} wrong passwords for one account lock it at that address for {minutes} minutes"
    try:
        locks = current_lockouts()
    except DatabaseError:  # not migrated yet: the database check says so
        return
    if not locks:
        yield Finding(Level.OK, title, f"{rule}; nothing is locked now")
        return
    listing = "; ".join(f"{lock.kind} {lock.name} (about {lock.minutes_left} min left)" for lock in locks)
    yield Finding(
        Level.WARN,
        "Admin logins are locked after wrong passwords",
        f"{rule}. Locked now: {listing}.",
        "If this was you, wait, or run `il2ks admin unlock --all` (or `--user NAME` / `--ip ADDRESS`). "
        "Repeated locks you did not cause mean somebody is guessing passwords: use a long password.",
    )
