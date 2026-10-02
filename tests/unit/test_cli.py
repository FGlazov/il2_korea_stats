import pytest

from il2ks.cli import main


def test_planned_commands_are_stubs(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["ingest"]) == 2
    assert "not implemented yet" in capsys.readouterr().err
