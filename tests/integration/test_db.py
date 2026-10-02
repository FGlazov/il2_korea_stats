"""DB harnesses: migrations, constraints, and SQLite <-> Postgres portability (TD-19)."""

import re
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.db import IntegrityError, transaction

from il2ks.db.copy import copy_all
from il2ks.db.models import GameObject, ObjectClass

SRC = Path(__file__).resolve().parents[2] / "src" / "il2ks"


@pytest.mark.django_db
def test_no_missing_migrations() -> None:
    out = StringIO()
    call_command("makemigrations", "--check", "--dry-run", stdout=out)


@pytest.mark.django_db
def test_check_constraint_rejects_invalid_class() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        GameObject.objects.create(log_name="X", display_name="X", cls="spaceship")


@pytest.mark.django_db
def test_unique_log_name() -> None:
    GameObject.objects.create(log_name="MiG-15bis", display_name="MiG-15bis")
    with pytest.raises(IntegrityError), transaction.atomic():
        GameObject.objects.create(log_name="MiG-15bis", display_name="duplicate")


def test_no_postgres_only_features_in_src() -> None:
    offenders = [
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if re.search(r"django\.contrib\.postgres|ArrayField|HStoreField|CIText", p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"Postgres-only features break SQLite portability (TD-19): {offenders}"


@pytest.mark.postgres
@pytest.mark.django_db(databases=["default", "sqlite"])
def test_copy_sqlite_to_postgres_keeps_rows_and_keys() -> None:
    created = [
        GameObject.objects.using("sqlite").create(log_name=f"obj-{i}", display_name=f"Object {i}", cls=ObjectClass.TANK)
        for i in range(5)
    ]

    counts = copy_all("sqlite", "default")

    assert counts[GameObject._meta.db_table] == len(created)
    copied = {o.pk: o.log_name for o in GameObject.objects.using("default").all()}
    assert copied == {o.pk: o.log_name for o in created}
    # Sequences were reset: a new row doesn't collide with copied primary keys.
    GameObject.objects.using("default").create(log_name="after-copy", display_name="After copy")
