import logging

import pytest
from fastapi.testclient import TestClient

from overlaparr import logging_setup
from overlaparr.main import create_app
from tests.fakes import FakePlexGateway

USER = {"username": "alice", "password": "hunter2hunter2"}


@pytest.fixture(autouse=True)
def _clean_logging():
    logging_setup._SECRETS.clear()
    yield
    logging_setup._SECRETS.clear()
    root = logging.getLogger()
    for h in [h for h in root.handlers if getattr(h, "_overlaparr", False)]:
        root.removeHandler(h)
        h.close()


@pytest.fixture
def gateway():
    return FakePlexGateway({"home": {}, "friend": {}, "other": {}})


@pytest.fixture
def make_app(tmp_path, gateway):
    def build(**kw):
        kw.setdefault("gateway_factory", lambda token: gateway)
        kw.setdefault("start_background", False)
        return create_app(tmp_path, **kw)

    return build


def wait_idle(app, timeout=20):
    """Block until no scan is running (scans run in a daemon thread)."""
    lock = app.state.scan_engine._lock
    assert lock.acquire(timeout=timeout)
    lock.release()


@pytest.fixture
def client(make_app):
    app = make_app()
    with TestClient(app) as c:
        yield c
        wait_idle(app)


@pytest.fixture
def authed(client):
    r = client.post("/api/v1/setup/user", json=USER)
    assert r.status_code == 200, r.text
    return client
