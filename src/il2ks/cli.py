"""The `il2ks` command (FR-OPS-1).

Exit codes: 0 = done, 1 = done but some missions failed, 2 = usage or configuration error,
3 = another writer holds the lock (FR-ING-20).
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from il2ks.config import Config, ConfigError, load_config

PLANNED: dict[str, tuple[str, str]] = {
    "setup": ("FR-OPS-1", "interactive first-time setup: writes il2ks.toml from the example"),
    "web": ("FR-OPS-1", "serve the website"),
    "run": ("FR-OPS-1", "web + watch + HTTPS proxy together"),
    "createadmin": ("FR-OPS-1", "create an admin account"),
    "doctor": ("FR-OPS-1", "check the configuration"),
    "backup": ("FR-OPS-6", "snapshot of the admin state (DB, config, custom/) into a dated zip"),
    "restore": ("FR-OPS-6", "restore a backup zip"),
}
"""Subcommands that exist only as stubs: name -> (requirement that plans it, one-line description)."""
EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_LOCKED = 0, 1, 2, 3

log = logging.getLogger("il2ks.cli")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _django_setup() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "il2ks.settings")
    import django

    django.setup()


def _iso_date(text: str) -> date:
    """argparse type for `YYYY-MM-DD` (strictly: not `20260401` or `2026-W14-3`)."""
    try:
        if _DATE_RE.fullmatch(text) is None:
            raise ValueError(text)
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a date, expected YYYY-MM-DD") from None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="il2ks", description="IL-2 Korea stats")
    parser.add_argument("--config", type=Path, help="il2ks.toml to use (default: IL2KS_CONFIG, ./il2ks.toml, data dir)")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, (requirement, description) in PLANNED.items():
        text = f"not implemented yet (planned: {requirement}): {description}"
        sub.add_parser(name, help=text, description=text)

    wait_help = "if another writer holds the lock, wait up to this many seconds instead of exiting"
    ingest = sub.add_parser("ingest", help="process every complete mission once, then exit")
    ingest.add_argument(
        "--from",
        dest="source",
        type=Path,
        help="import from this folder or file instead of the log folder (concatenated .txt/.txt.zip archives or parts)",
    )
    ingest.add_argument("--wait", type=float, metavar="SECONDS", help=wait_help)

    sub.add_parser("watch", help="run ingest every N seconds until Ctrl+C")

    reprocess = sub.add_parser("reprocess", help="re-run missions from their archives, then rebuild aggregates")
    reprocess.add_argument("--mission", action="append", metavar="UID", help="only this mission (repeatable)")
    span_note = "The date is the one in the mission UID, i.e. the server's local time (not UTC). Inclusive."
    reprocess.add_argument(
        "--since", type=_iso_date, metavar="YYYY-MM-DD", help=f"only missions on or after this date. {span_note}"
    )
    reprocess.add_argument(
        "--until", type=_iso_date, metavar="YYYY-MM-DD", help=f"only missions on or before this date. {span_note}"
    )
    reprocess.add_argument("--workers", type=int, help="parse/replay worker processes (default: CPUs - 1)")
    reprocess.add_argument("--wait", type=float, metavar="SECONDS", help=wait_help)

    rebuild = sub.add_parser("rebuild-aggregates", help="recompute level-2 tables from level 1")
    rebuild.add_argument("--wait", type=float, metavar="SECONDS", help=wait_help)

    sub.add_parser("manage", help="run a Django management command")
    db = sub.add_parser("db", help="database tools").add_subparsers(dest="db_command", required=True)
    db_copy = db.add_parser("copy", help="copy all tables between database aliases (TD-19)")
    db_copy.add_argument("--from", dest="source", default="default")
    db_copy.add_argument("--to", dest="target", required=True)
    dev = sub.add_parser("dev", help="developer tools").add_subparsers(dest="dev_command", required=True)
    anon = dev.add_parser("anonymize", help="anonymize a mission log for test fixtures")
    anon.add_argument("source", type=Path, help="mission .txt or .txt.zip")
    anon.add_argument("target", type=Path, help="output .txt.zip")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "manage":
        # Pass everything after "manage" to Django (migrate, createsuperuser, ...).
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "il2ks.settings")
        from django.core.management import execute_from_command_line

        execute_from_command_line(["il2ks manage", *args[1:]])
        return EXIT_OK

    ns = _build_parser().parse_args(args)
    command: str = ns.command
    if command == "dev" and ns.dev_command == "anonymize":
        from il2ks.devtools.anonymize import anonymize_file

        anonymize_file(ns.source, ns.target)
        return EXIT_OK
    if command == "db" and ns.db_command == "copy":
        _django_setup()
        from il2ks.db.copy import copy_all

        counts = copy_all(ns.source, ns.target)
        for table, n in counts.items():
            print(f"{table}: {n}")
        return EXIT_OK
    if command in {"ingest", "watch", "reprocess", "rebuild-aggregates"}:
        return _writer_command(command, ns)
    print(f"il2ks {command}: not implemented yet (planned: {PLANNED[command][0]})", file=sys.stderr)
    return EXIT_USAGE


def _writer_command(command: str, ns: argparse.Namespace) -> int:
    """Commands that write the DB: config, logging, writer lock, migrations, then the job."""
    if ns.command == "reprocess" and ns.since is not None and ns.until is not None and ns.since > ns.until:
        print(f"il2ks: --since {ns.since} is after --until {ns.until}", file=sys.stderr)
        return EXIT_USAGE
    try:
        cfg = load_config(ns.config)
    except ConfigError as exc:
        print(f"il2ks: configuration error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    source: Path | None = getattr(ns, "source", None)
    if command in {"ingest", "watch"} and source is None and cfg.logs.dir is None:
        print("il2ks: logs.dir is not configured ([logs] dir in il2ks.toml, or IL2KS_LOGS_DIR)", file=sys.stderr)
        return EXIT_USAGE
    if source is not None and not source.exists():
        print(f"il2ks: --from: {source} does not exist", file=sys.stderr)
        return EXIT_USAGE
    if command in {"ingest", "watch"} and source is None and cfg.logs.dir is not None and not cfg.logs.dir.is_dir():
        print(f"il2ks: log folder {cfg.logs.dir} does not exist", file=sys.stderr)
        return EXIT_USAGE

    # settings.py reads the data dir from the environment; make it match the config before Django starts.
    os.environ["IL2KS_DATA_DIR"] = str(cfg.data_dir)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    from il2ks import logsetup

    logsetup.configure_logging(
        command, cfg.log_dir, cfg.log_level, server_uid=cfg.server_uid, keep_days=cfg.log_keep_days
    )
    _django_setup()

    from il2ks.ingest.lock import LockBusyError

    wait: float | None = getattr(ns, "wait", None)
    try:
        _migrate_if_needed(cfg, command, wait)
        return _run_job(command, ns, cfg, source, wait)
    except LockBusyError as exc:
        print(f"il2ks {command}: {exc}", file=sys.stderr)
        return EXIT_LOCKED
    except KeyboardInterrupt:
        log.info("stopped")
        return EXIT_OK


def _migrate_if_needed(cfg: Config, command: str, wait: float | None) -> None:
    """FR-OPS-3: apply pending migrations before writing, under the writer lock."""
    from django.core.management import call_command
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    from il2ks.ingest.lock import WriterLock

    with WriterLock(cfg.data_dir, command, wait=wait):
        executor = MigrationExecutor(connection)
        if executor.migration_plan(executor.loader.graph.leaf_nodes()):
            log.info("applying database migrations")
            call_command("migrate", interactive=False, verbosity=0)


def _run_job(command: str, ns: argparse.Namespace, cfg: Config, source: Path | None, wait: float | None) -> int:
    from il2ks.ingest import reprocess as reprocess_mod
    from il2ks.ingest import runner
    from il2ks.ingest import watch as watch_mod

    if command == "ingest":
        opts = runner.IngestOptions(source=source, lock_wait=wait)
        summary = runner.ingest_once(cfg, runner.default_pipeline(cfg), opts)
        print(summary.describe())
        return EXIT_FAILED if summary.failed else EXIT_OK
    if command == "watch":
        watch_mod.watch(cfg, runner.default_pipeline(cfg))
        return EXIT_OK
    if command == "reprocess":
        result = reprocess_mod.reprocess(
            cfg,
            runner.default_pipeline(cfg),
            ns.mission,
            since=ns.since,
            until=ns.until,
            workers=ns.workers,
            lock_wait=wait,
        )
        print(result.describe())
        return EXIT_FAILED if (result.failed or result.missing) else EXIT_OK
    reprocess_mod.rebuild_all(cfg, lock_wait=wait)
    print("level-2 aggregates rebuilt")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
