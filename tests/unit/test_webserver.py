"""`il2ks web` hands hypercorn what a server behind a proxy needs (TD-10)."""

from pathlib import Path
from typing import Any, cast

import pytest
from hypercorn.config import Config as HypercornConfig

from il2ks.config import load_config
from il2ks.serving import webserver

CADDY_IDLE_KEEPALIVE_S = 120


def config_for(tmp_path: Path, host: str = "127.0.0.1", port: int = 8123, workers: int = 3) -> HypercornConfig:
    cfg = load_config(None, {"IL2KS_DATA_DIR": str(tmp_path)}, create_server_uid=False)
    return webserver.hypercorn_config(cfg, host, port, workers=workers)


def test_hypercorn_serves_the_app_at_the_asked_address(tmp_path: Path) -> None:
    config = config_for(tmp_path)

    assert config.application_path == "il2ks.asgi:application"
    assert config.bind == ["127.0.0.1:8123"]
    assert config.workers == 3


def test_an_ipv6_address_is_bracketed(tmp_path: Path) -> None:
    assert config_for(tmp_path, host="::1").bind == ["[::1]:8123"]


def test_serving_runs_hypercorn_with_this_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[HypercornConfig] = []
    monkeypatch.setattr("hypercorn.run.run", seen.append)
    cfg = load_config(None, {"IL2KS_DATA_DIR": str(tmp_path)}, create_server_uid=False)

    webserver.serve_hypercorn(cfg, "127.0.0.1", 8123, workers=2)

    assert [config.workers for config in seen] == [2]


def test_the_proxy_closes_idle_connections_first(tmp_path: Path) -> None:
    """A server that closes first can race the proxy reusing that connection, and the visitor sees a 502."""
    assert config_for(tmp_path).keep_alive_timeout > CADDY_IDLE_KEEPALIVE_S


def test_requests_in_flight_get_time_to_finish_at_a_stop(tmp_path: Path) -> None:
    assert config_for(tmp_path).graceful_timeout == webserver.STOP_WAIT_S


def test_hypercorn_logs_into_our_files_and_the_proxy_writes_the_access_log(tmp_path: Path) -> None:
    config = config_for(tmp_path)

    assert config.accesslog is None
    assert config.errorlog is None
    logging_setup = cast("dict[str, Any]", config.logconfig_dict)  # pyright: ignore[reportUnknownMemberType]
    assert "file" in logging_setup["handlers"]
    for name in webserver.HYPERCORN_LOGGERS:
        assert logging_setup["loggers"][name] == {"handlers": [], "propagate": True}, name
