"""Where the data version is bumped (TD-28): mission saves, rebuilds, and (see test_admin) admin edits."""

from datetime import UTC, datetime

import pytest
from django.db import transaction

from il2ks.db.models import DataVersion
from il2ks.db.site import bump_data_version, current_data_version
from il2ks.ingest.aggregates import rebuild_aggregates
from tests.factories import mission, save, sortie

pytestmark = pytest.mark.django_db


def test_saving_a_mission_bumps_the_version() -> None:
    before = current_data_version()

    save(mission((sortie(0, 1),)))

    assert current_data_version() > before


def test_the_bump_is_in_the_missions_transaction() -> None:
    """A mission save that rolls back must not leave a bumped version behind."""
    before = current_data_version()

    def save_then_fail() -> None:
        with transaction.atomic():
            save(mission((sortie(0, 1),)))
            raise RuntimeError("the surrounding transaction fails after the save")

    with pytest.raises(RuntimeError):
        save_then_fail()

    assert current_data_version() == before


def test_resaving_the_same_mission_bumps_again() -> None:
    """A reprocess changes what pages show even though the mission row already exists."""
    result = mission((sortie(0, 1),))
    save(result)
    after_first = current_data_version()

    save(result)

    assert current_data_version() > after_first


def test_rebuild_aggregates_bumps_the_version() -> None:
    save(mission((sortie(0, 1),)))
    before = current_data_version()

    rebuild_aggregates()

    assert current_data_version() > before


def test_a_bump_advances_the_updated_at_timestamp() -> None:
    """The footer's "Data updated" comes from `updated_at`; `QuerySet.update()` skips `auto_now` (TD-28)."""
    bump_data_version()
    DataVersion.objects.filter(pk=1).update(updated_at=datetime(2000, 1, 1, tzinfo=UTC))

    bump_data_version()

    assert DataVersion.objects.get(pk=1).updated_at > datetime(2020, 1, 1, tzinfo=UTC)
