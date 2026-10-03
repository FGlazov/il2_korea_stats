"""Migration 0010 flags the names an admin typed before `name_overridden` existed (TD-24)."""

from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection

from il2ks.core.catalog.loader import load_default_catalog
from il2ks.db.models import GameObject

pytestmark = pytest.mark.django_db


def test_only_names_that_differ_from_the_shipped_default_become_overrides() -> None:
    default = load_default_catalog().lookup("F-86A-5").display_name
    GameObject.objects.create(log_name="F-86A-5", display_name=default)
    GameObject.objects.create(log_name="MiG-15bis", display_name="My MiG")  # typed by an admin
    GameObject.objects.create(log_name="Mystery-1", display_name="Mystery-1", is_known=False)  # auto-registered
    GameObject.objects.create(log_name="Mystery-2", display_name="Mystery Jet", is_known=False)  # named in the admin
    migration = import_module("il2ks.db.migrations.0010_gameobject_name_overridden")

    migration.flag_existing_overrides(apps, SimpleNamespace(connection=connection))

    flags = dict(GameObject.objects.values_list("log_name", "name_overridden"))
    assert flags == {"F-86A-5": False, "MiG-15bis": True, "Mystery-1": False, "Mystery-2": True}
