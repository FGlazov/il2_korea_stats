"""The `il2ks` command (FR-OPS-1). Iteration 0: real `manage` and `dev anonymize`; the rest are stubs."""

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

PLANNED = ["setup", "ingest", "watch", "web", "run", "reprocess", "rebuild-aggregates", "doctor", "backup", "restore"]


def _django_setup() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "il2ks.settings")
    import django

    django.setup()


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "manage":
        # Pass everything after "manage" to Django (migrate, createsuperuser, ...).
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "il2ks.settings")
        from django.core.management import execute_from_command_line

        execute_from_command_line(["il2ks manage", *args[1:]])
        return 0

    parser = argparse.ArgumentParser(prog="il2ks", description="IL-2 Korea stats")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in PLANNED:
        sub.add_parser(name, help="not implemented yet")
    sub.add_parser("manage", help="run a Django management command")
    db = sub.add_parser("db", help="database tools").add_subparsers(dest="db_command", required=True)
    db_copy = db.add_parser("copy", help="copy all tables between database aliases (TD-19)")
    db_copy.add_argument("--from", dest="source", default="default")
    db_copy.add_argument("--to", dest="target", required=True)
    dev = sub.add_parser("dev", help="developer tools").add_subparsers(dest="dev_command", required=True)
    anon = dev.add_parser("anonymize", help="anonymize a mission log for test fixtures")
    anon.add_argument("source", type=Path, help="mission .txt or .txt.zip")
    anon.add_argument("target", type=Path, help="output .txt.zip")

    ns = parser.parse_args(args)
    if ns.command == "dev" and ns.dev_command == "anonymize":
        from il2ks.devtools.anonymize import anonymize_file

        anonymize_file(ns.source, ns.target)
        return 0
    if ns.command == "db" and ns.db_command == "copy":
        _django_setup()
        from il2ks.db.copy import copy_all

        counts = copy_all(ns.source, ns.target)
        for table, n in counts.items():
            print(f"{table}: {n}")
        return 0
    print(f"il2ks {ns.command}: not implemented yet (see design_doc/10_roadmap.md)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
