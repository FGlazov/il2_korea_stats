import pytest
from django.test import Client

from il2ks.db.models import GameObject, ObjectClass
from tests.simple_reads import assert_simple_reads


@pytest.mark.django_db
def test_home_page_renders_with_simple_reads(client: Client) -> None:
    GameObject.objects.create(
        log_name="F-86A-5", display_name="F-86A-5 Sabre", cls=ObjectClass.FIGHTER, is_playable=True
    )

    assert_simple_reads(client, "/", max_queries=2)

    assert "F-86A-5 Sabre" in client.get("/").content.decode()
