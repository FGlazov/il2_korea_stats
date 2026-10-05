"""The `il2ks` command (FR-OPS-1).

Exit codes (`il2ks.exitcodes`): 0 = done, 1 = done but some missions failed, 2 = usage or configuration error or a
refused action, 3 = another writer holds the lock (FR-ING-20). `il2ks doctor` reports its verdict the same way:
0 = all OK, 1 = warnings only, 2 = at least one error.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import re
import sys
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

from il2ks import __version__
from il2ks.config import Config, ConfigError, load_config
from il2ks.exitcodes import EXIT_FAILED, EXIT_LOCKED, EXIT_OK, EXIT_USAGE
from il2ks.ops.migrate import migrate_if_needed
from il2ks.ops.setup import HTTPS_MODES
from il2ks.serving import commands as serving_commands
from il2ks.serving import procutil

PLANNED: dict[str, tuple[str, str]] = {}
"""Subcommands that exist only as stubs: name -> (requirement that plans it, one-line description)."""

OPS_COMMANDS = frozenset({"setup", "createadmin", "doctor", "backup", "restore"})  # handlers: il2ks.ops.commands

log = logging.getLogger("il2ks.cli")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _django_setup() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "il2ks.settings")
    import django

    django.setup()


def _setup_pending() -> bool:
    """No admin account yet (so `web` creates the setup token); a database problem is not the setup page's business."""
    from django.db import DatabaseError

    from il2ks.ops import admin

    try:
        return not admin.admin_exists()
    except DatabaseError:
        return False


def _iso_date(text: str) -> date:
    """argparse type for `YYYY-MM-DD` (strictly: not `20260401` or `2026-W14-3`)."""
    try:
        if _DATE_RE.fullmatch(text) is None:
            raise ValueError(text)
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a date, expected YYYY-MM-DD") from None


type SubParsers = argparse._SubParsersAction[argparse.ArgumentParser]  # pyright: ignore[reportPrivateUsage]


def _add_ops_parsers(sub: SubParsers) -> None:
    """setup, createadmin, doctor, backup, restore (FR-OPS-1, FR-OPS-6); handlers in `il2ks.ops.commands`."""
    env_note = "Every answer can also come from an environment variable; flags win."
    setup = sub.add_parser(
        "setup",
        help="first-time setup: asks a few questions, writes il2ks.toml, creates the database and the admin",
        description=(
            "First-time setup. Asks for the data folder, DServer's text log folder, the server's time zone and the "
            "website address, writes il2ks.toml (the example file with your answers), creates the data folder and "
            "database and the admin account. Refuses to overwrite an existing il2ks.toml unless --force (the old "
            "file is then saved next to it). Without a terminal, or with --non-interactive, nothing is asked. "
            + env_note
        ),
    )
    setup.add_argument("--data-dir", help="data folder [IL2KS_DATA_DIR]; default ./.il2ks-data")
    setup.add_argument("--logs-dir", help="DServer's text log folder [IL2KS_LOGS_DIR]")
    setup.add_argument(
        "--timezone", help="IANA name of the server's time zone, e.g. Europe/Berlin [IL2KS_SERVER_TIMEZONE]"
    )
    setup.add_argument("--domain", help="the website's domain name [IL2KS_HTTPS_DOMAIN]")
    setup.add_argument(
        "--https", choices=HTTPS_MODES, help="caddy = bundled HTTPS proxy, external = your own [IL2KS_HTTPS_MODE]"
    )
    setup.add_argument("--email", help="e-mail for certificate notices (caddy mode) [IL2KS_HTTPS_EMAIL]")
    setup.add_argument("--admin-username", help="admin account name [IL2KS_ADMIN_USERNAME]; default admin")
    setup.add_argument(
        "--admin-password-file", type=Path, help="file holding the admin password [IL2KS_ADMIN_PASSWORD]"
    )
    setup.add_argument("--no-admin", action="store_true", help="do not create an admin account")
    setup.add_argument("--non-interactive", action="store_true", help="never ask; use flags, environment and defaults")
    setup.add_argument("--force", action="store_true", help="replace an existing il2ks.toml (a copy is kept)")

    admin_parser = sub.add_parser(
        "createadmin",
        help="create an admin account, or reset the password of an existing one",
        description=(
            "Create an admin (superuser) account, or reset an existing account's password and make it an admin. "
            "In a terminal it asks for the password twice. For scripts give --username and the password in the "
            "IL2KS_ADMIN_PASSWORD environment variable or --password-file."
        ),
    )
    admin_parser.add_argument("--username", help="account name (asked for in a terminal, default admin)")
    admin_parser.add_argument("--password-file", type=Path, help="file holding the password (first line break dropped)")
    admin_parser.add_argument("--email", help="e-mail address of the account (optional)")
    admin_parser.add_argument(
        "--if-none",
        action="store_true",
        help="do nothing when an admin account already exists (for first-start scripts such as the Docker image)",
    )
    admin_parser.add_argument(
        "--wait", type=float, default=30.0, metavar="SECONDS", help="how long to wait for another writer"
    )

    sub.add_parser(
        "doctor",
        help="check the setup and say what to fix",
        description=(
            "Checks the configuration, data folder, database, log folder, time zone, server ID, disk space, ingestion "
            "history and backups. Exit code: 0 = all OK, 1 = warnings only, 2 = at least one error."
        ),
    ).add_argument("--json", action="store_true", help="machine-readable output")

    sub.add_parser(
        "backup",
        help="write a backup zip of the database, config, custom/ and media/ to <data dir>/backups",
        description=(
            "Backs up what the mission logs cannot rebuild: a consistent copy of the SQLite database, il2ks.toml, "
            "custom/, media/ and the server ID. Not the archived mission logs. Keeps the newest [backup] keep zips. "
            "Backups are also made before database updates and, by 'il2ks watch', once a day."
        ),
    )
    restore = sub.add_parser(
        "restore",
        help="restore a backup zip (database, config, custom/, media/)",
        description=(
            "Restores a backup made by 'il2ks backup'. Backs up the current state first, refuses while another "
            "il2ks writer (watch, ingest) or the stack (il2ks run) is running unless --force, and checks the result. "
            "SQLite only."
        ),
    )
    restore.add_argument("zip", type=Path, help="the backup zip")
    restore.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    restore.add_argument(
        "--force", action="store_true", help="restore even though il2ks seems to be running (stop it first if you can)"
    )


def _add_translation_parsers(dev: SubParsers) -> None:
    """`il2ks dev translations ...` (TD-24, docs/translating.md): pure-Python gettext workflow."""
    tr = dev.add_parser("translations", help="extract, merge, compile and check the translations (docs/translating.md)")
    sub = tr.add_subparsers(dest="translations_command", required=True)
    sub.add_parser("update", help="re-extract the strings, merge them into every language's .po, then compile")
    sub.add_parser("compile", help="compile every .po into its .mo (after editing a .po by hand)")
    sub.add_parser("check", help="change nothing; exit 1 when update/compile would change a file (CI, pre-commit)")
    sub.add_parser("status", help="per language: strings, translated, still llm-draft (unreviewed), untranslated")
    missing = sub.add_parser("missing", help="print a language's untranslated strings as JSON (input for a draft)")
    missing.add_argument("language", help="ru, de, es, fr or pt-br")
    imp = sub.add_parser("import", help="fill untranslated strings from a JSON file {msgid: msgstr}, marked llm-draft")
    imp.add_argument("language", help="ru, de, es, fr or pt-br")
    imp.add_argument("file", type=Path, help="JSON object: msgid -> msgstr (a list of forms for plural messages)")


def _translations_command(ns: argparse.Namespace) -> int:
    import json

    from il2ks.devtools import translations

    try:
        match ns.translations_command:
            case "update":
                for directory, count in translations.update().items():
                    print(f"{directory}: {count} strings")
                translations.compile_all()
            case "check":
                problems = translations.check()
                for problem in problems:
                    print(f"  - {problem}")
                print("translations are up to date" if not problems else "run `uv run il2ks dev translations update`")
                return EXIT_FAILED if problems else EXIT_OK
            case "compile":
                for path in translations.compile_all():
                    print(f"wrote {path}")
            case "status":
                print(f"{'language':<9}{'strings':>8}{'translated':>11}{'llm-draft':>10}{'reviewed':>9}{'missing':>8}")
                for row in translations.status():
                    print(
                        f"{row.language:<9}{row.total:>8}{row.translated:>11}{row.drafts:>10}"
                        f"{row.reviewed:>9}{row.untranslated:>8}"
                    )
            case "missing":
                text = json.dumps(translations.missing(ns.language), ensure_ascii=False, indent=2)
                sys.stdout.buffer.write(text.encode("utf-8") + b"\n")  # UTF-8 whatever the console code page is
            case _:  # "import"
                filled = translations.import_drafts(ns.language, translations.load_drafts(ns.file))
                print(
                    f"{filled} strings filled, marked {translations.DRAFT_MARK}; run `il2ks dev translations compile`"
                )
    except translations.TranslationError as exc:
        print(f"il2ks dev translations: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="il2ks", description="IL-2 Korea stats")
    parser.add_argument("--config", type=Path, help="il2ks.toml to use (default: IL2KS_CONFIG, ./il2ks.toml, data dir)")
    parser.add_argument("--version", action="version", version=f"il2ks {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, (requirement, description) in PLANNED.items():
        text = f"not implemented yet (planned: {requirement}): {description}"
        sub.add_parser(name, help=text, description=text)

    _add_ops_parsers(sub)

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

    reprocess = sub.add_parser(
        "reprocess",
        help="re-run missions from their archives, then rebuild aggregates (choose: --all, --mission, --since/--until)",
        description=(
            "Re-runs missions from their archives with the current rules, then rebuilds the aggregates. It can take a "
            "long time on a big archive, so you must choose what to reprocess: --all for every mission, or narrow it "
            "with --mission and/or --since/--until. Without any of these, nothing is done."
        ),
    )
    reprocess.set_defaults(reprocess_parser=reprocess)
    reprocess.add_argument(
        "--all", action="store_true", help="reprocess every mission (explicit, can take a long time)"
    )
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
    rebuild.add_argument(
        "--retour",
        action="store_true",
        help="first move every mission to its tour under the [tours] config (after changing mode, start or timezone)",
    )
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
    bump = dev.add_parser(
        "bump-templates",
        help="after editing built-in templates/CSS/JS: add version lines, raise versions, rewrite the registry",
    )
    bump.add_argument("--check", action="store_true", help="change nothing; exit 1 if something would change")
    changes = dev.add_parser(
        "template-changes", help="template versions that changed since a release tag (release notes)"
    )
    changes.add_argument("old_tag", help="a git tag or commit, like v0.2.0")
    bench = dev.add_parser("bench-ingest", help="time an import of a log folder, split by phase (throw-away data dir)")
    bench.add_argument("source", type=Path, help="folder with mission logs (.txt / .txt.zip)")
    bench.add_argument("--limit", type=int, metavar="N", help="only the first N missions")
    bench.add_argument("--profile", type=Path, metavar="FILE", help="write a cProfile dump of the whole run")
    bench.add_argument("--data-dir", type=Path, metavar="DIR", help="keep the resulting data dir here (for dump-db)")
    bench.add_argument("--cpu", action="store_true", help="time process CPU instead of wall clock (noisy machines)")
    bench.add_argument("--per-mission", action="store_true", help="level 2 after every mission, never batched")
    dump = dev.add_parser(
        "dump-db", help="write every table of a data dir's database as sorted JSON lines, to compare runs"
    )
    dump.add_argument("data_dir", type=Path, help="a data dir with il2ks.sqlite3")
    dump.add_argument("target", type=Path, help="output file (.jsonl)")
    assets = dev.add_parser(
        "assets", help="inventory of every image the site uses or ships (design_doc/15, the designer brief)"
    )
    assets.add_argument("--check", action="store_true", help="exit 1 when the brief, the code and static/ disagree")
    assets.add_argument("--write", action="store_true", help="rewrite the generated inventory in design_doc/15")
    _add_translation_parsers(dev)
    chk = dev.add_parser(
        "check",
        help="everything to run before committing (lint, types, guards, tests); see CLAUDE.md. Default tier: quick",
    )
    tier = chk.add_mutually_exclusive_group()
    tier.add_argument("--fast", action="store_true", help="static checks + guards only (~15 s; the Stop hook)")
    tier.add_argument("--quick", action="store_true", help="fast + unit tests (the default; before every commit)")
    tier.add_argument("--full", action="store_true", help="quick + integration tests (before merging / finishing)")
    chk.add_argument(
        "--fix", action="store_true", help="first run the auto-fixers: ruff --fix/format, bump-templates, translations"
    )
    chk.add_argument("--no-tests", action="store_true", help="skip every test step (CI lint job)")
    chk.add_argument("--postgres", action="store_true", help="also run the whole suite on Postgres (compose.dev.yaml)")
    chk.add_argument("--e2e", action="store_true", help="also run the Playwright browser tests")
    chk.add_argument("--only", action="append", default=[], metavar="STEP", help="just this step (repeatable)")
    chk.add_argument("--list", action="store_true", dest="list_steps", help="list the steps of the chosen tier, exit")
    chk.add_argument("--if-changed", action="store_true", help="skip when nothing changed since the last passing run")
    chk.add_argument("--verbose", action="store_true", help="show the whole output of failed steps")
    dev.add_parser(
        "check-migrations",
        help="released migrations unchanged (vs origin/main or main), exactly one leaf, no duplicate numbers",
    )
    serving_commands.add_parsers(sub)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command. Ctrl+C / SIGTERM at any point (also while starting up) is a clean stop, not a traceback."""
    try:
        return _main(argv)
    except KeyboardInterrupt:
        return EXIT_OK


def _main(argv: Sequence[str] | None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "manage":
        # Pass everything after "manage" to Django (migrate, createsuperuser, ...).
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "il2ks.settings")
        # A first `il2ks manage migrate` on a fresh machine: SQLite can't create the folder it lives in. Any problem
        # here is left for Django to report in its own words.
        with contextlib.suppress(ConfigError, OSError):
            load_config(create_server_uid=False).data_dir.mkdir(parents=True, exist_ok=True)
        from django.core.management import execute_from_command_line

        execute_from_command_line(["il2ks manage", *args[1:]])
        return EXIT_OK

    ns = _build_parser().parse_args(args)
    command: str = ns.command
    if command in serving_commands.COMMANDS:
        hooks = serving_commands.Hooks(
            django_setup=_django_setup,
            migrate=migrate_if_needed,
            setup_pending=_setup_pending,
            contain_children=procutil.kill_children_when_we_die,
        )
        return serving_commands.dispatch(ns, hooks)
    if command == "watch":
        procutil.terminate_as_keyboard_interrupt()  # `il2ks run` stops its children with SIGTERM / Ctrl+Break
    if command == "dev" and ns.dev_command == "anonymize":
        from il2ks.devtools.anonymize import anonymize_file

        anonymize_file(ns.source, ns.target)
        return EXIT_OK
    if command == "dev" and ns.dev_command == "bailout-eval":
        from il2ks.devtools.bailout_eval import run as run_bailout_eval

        return run_bailout_eval(ns.directory, list_disagreements=ns.list)
    if command == "dev" and ns.dev_command == "bench-ingest":
        from il2ks.devtools.bench import bench_ingest

        return bench_ingest(
            ns.source, limit=ns.limit, profile=ns.profile, data_dir=ns.data_dir, cpu=ns.cpu, per_mission=ns.per_mission
        )
    if command == "dev" and ns.dev_command == "dump-db":
        from il2ks.devtools.dbdump import dump_db

        return dump_db(ns.data_dir, ns.target)
    if command == "dev" and ns.dev_command == "bump-templates":
        from il2ks.devtools.templates import bump_templates

        return bump_templates(check=ns.check)
    if command == "dev" and ns.dev_command == "assets":
        _django_setup()
        from il2ks.devtools.assets import run as run_assets

        return run_assets(check=ns.check, write=ns.write)
    if command == "dev" and ns.dev_command == "template-changes":
        from il2ks.devtools.templates import template_changes

        return template_changes(ns.old_tag)
    if command == "dev" and ns.dev_command == "check":
        from il2ks.devtools.check import Tier, check

        chosen: Tier = "full" if ns.full else "fast" if ns.fast else "quick"
        return check(
            chosen,
            fix=ns.fix,
            no_tests=ns.no_tests,
            postgres=ns.postgres,
            e2e=ns.e2e,
            only=ns.only,
            if_changed=ns.if_changed,
            verbose=ns.verbose,
            list_only=ns.list_steps,
        )
    if command == "dev" and ns.dev_command == "check-migrations":
        from il2ks.devtools import migrations as dev_migrations
        from il2ks.devtools.check import repo_root

        return dev_migrations.check(repo_root())
    if command == "dev" and ns.dev_command == "translations":
        return _translations_command(ns)
    if command == "db" and ns.db_command == "copy":
        _django_setup()
        from il2ks.db.copy import copy_all

        counts = copy_all(ns.source, ns.target)
        for table, n in counts.items():
            print(f"{table}: {n}")
        return EXIT_OK
    if command in {"ingest", "watch", "reprocess", "rebuild-aggregates"}:
        return _writer_command(command, ns)
    if command in OPS_COMMANDS:
        from il2ks.ops import commands

        handlers: dict[str, Callable[[argparse.Namespace], int]] = {
            "setup": commands.cmd_setup,
            "createadmin": commands.cmd_createadmin,
            "doctor": commands.cmd_doctor,
            "backup": commands.cmd_backup,
            "restore": commands.cmd_restore,
        }
        return handlers[command](ns)
    print(f"il2ks {command}: not implemented yet (planned: {PLANNED[command][0]})", file=sys.stderr)
    return EXIT_USAGE


def _writer_command(command: str, ns: argparse.Namespace) -> int:
    """Commands that write the DB: config, logging, writer lock, migrations, then the job."""
    if ns.command == "reprocess" and not (ns.all or ns.mission or ns.since is not None or ns.until is not None):
        ns.reprocess_parser.print_help(sys.stderr)
        print(
            "\nil2ks: choose what to reprocess: --all, --mission UID or --since/--until (nothing was reprocessed)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if ns.command == "reprocess" and ns.all and (ns.mission or ns.since is not None or ns.until is not None):
        print("il2ks: --all cannot be combined with --mission, --since or --until", file=sys.stderr)
        return EXIT_USAGE
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
        migrate_if_needed(cfg, command, wait)  # FR-OPS-3, after a backup when the database has data (FR-OPS-6)
        return _run_job(command, ns, cfg, source, wait)
    except LockBusyError as exc:
        print(f"il2ks {command}: {exc}", file=sys.stderr)
        return EXIT_LOCKED
    except KeyboardInterrupt:
        log.info("stopped")
        return EXIT_OK


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
            runner.default_pipeline(cfg, defer_ratings=True),
            ns.mission,
            since=ns.since,
            until=ns.until,
            workers=ns.workers,
            lock_wait=wait,
        )
        print(result.describe())
        return EXIT_FAILED if (result.failed or result.missing) else EXIT_OK
    reprocess_mod.rebuild_all(cfg, reassign_tours=ns.retour, lock_wait=wait)
    print("level-2 aggregates rebuilt" + (" (missions reassigned to tours)" if ns.retour else ""))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
