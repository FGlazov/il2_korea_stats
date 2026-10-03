"""One id per `il2ks web` start, shared by all its worker processes (TD-28).

The ETag of a page includes it, so a restart (which is how template and static overrides in `custom/` take effect)
invalidates what browsers revalidate. The `il2ks web` parent exports a fresh id before it starts the workers, which
inherit the environment; with `[web] workers > 1` every worker then builds the same ETag for the same page, so a
conditional request that lands on another worker still gets its 304. A process started without the parent (tests,
`manage.py runserver`) falls back to an id of its own.
"""

import os
import secrets

BOOT_ID_ENV = "IL2KS_BOOT_ID"


def export_boot_id() -> str:
    """Called once by the `il2ks web` parent, before any worker starts. Returns the new id."""
    boot_id = secrets.token_hex(8)
    os.environ[BOOT_ID_ENV] = boot_id
    return boot_id


def current_boot_id() -> str:
    """The id the parent exported, else a fresh per-process one."""
    return os.environ.get(BOOT_ID_ENV) or secrets.token_hex(8)
