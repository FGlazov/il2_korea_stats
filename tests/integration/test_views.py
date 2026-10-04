import pytest
from django.test import Client

from tests.simple_reads import HOME_READS_EMPTY, assert_simple_reads


@pytest.mark.django_db
def test_home_page_renders_on_an_empty_database_with_simple_reads(client: Client) -> None:
    """The home page's own contents are tested in test_mission_pages.py."""
    assert_simple_reads(client, "/", max_queries=HOME_READS_EMPTY)  # see tests/simple_reads.py

    assert "Latest missions" in client.get("/").content.decode()
