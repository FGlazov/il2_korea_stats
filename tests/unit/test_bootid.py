"""The ETag salt shared by the web workers (TD-28)."""

import pytest

from il2ks.serving.bootid import BOOT_ID_ENV, current_boot_id, export_boot_id


@pytest.fixture(autouse=True)
def _no_boot_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start without an id, and put the environment back afterwards whatever `export_boot_id` did."""
    monkeypatch.setenv(BOOT_ID_ENV, "")  # registers the undo
    monkeypatch.delenv(BOOT_ID_ENV)


def test_a_worker_reads_the_id_its_parent_exported() -> None:
    exported = export_boot_id()

    assert current_boot_id() == exported
    assert current_boot_id() == exported  # stable, not regenerated per call


def test_every_export_is_a_new_id() -> None:
    assert export_boot_id() != export_boot_id()


def test_without_a_parent_each_process_makes_up_an_id() -> None:
    assert current_boot_id()
