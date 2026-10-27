import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from overlaparr import auth
from overlaparr.config.schema import AppConfig
from overlaparr.db.models import User
from overlaparr.db.session import init_db, make_engine, make_sessionmaker

SECRET = "s3cret-signing-key"
API_KEY = "api-key-123"


class FakeStore:
    def __init__(self, bypass=False):
        self.current = AppConfig.model_validate({"auth": {"local_bypass": bypass}})

    def get_secret(self, key):
        return {"session_secret": SECRET, "app_api_key": API_KEY}.get(key)


@pytest.fixture
def dbf(tmp_path):
    eng = make_engine(tmp_path / "o.db")
    init_db(eng)
    yield make_sessionmaker(eng)
    eng.dispose()


def make_client(store, dbf, peer=("203.0.113.5", 1234)):
    app = FastAPI()
    dep = auth.make_require_auth(store, dbf)

    @app.get("/p")
    def p(principal: str = Depends(dep)):
        return {"principal": principal}

    return TestClient(app, client=peer)


def with_user(dbf):
    with dbf() as db:
        auth.create_user(db, "alice", "hunter2hunter2")


def test_password_hashed(dbf):
    with dbf() as db:
        assert auth.needs_setup(db)
        auth.create_user(db, "  alice ", "hunter2hunter2")
        u = db.query(User).one()
        assert u.username == "alice"
        assert u.password_hash != "hunter2hunter2"
        assert u.password_hash.startswith("$2")
        assert not auth.needs_setup(db)
        assert auth.verify(db, "alice", "hunter2hunter2")
        assert not auth.verify(db, "alice", "wrong-password")
        assert not auth.verify(db, "bob", "hunter2hunter2")
        with pytest.raises(ValueError):
            auth.create_user(db, "bob", "longenough")  # single user


def test_create_user_validation(dbf):
    with dbf() as db:
        with pytest.raises(ValueError):
            auth.create_user(db, "alice", "short")
        with pytest.raises(ValueError):
            auth.create_user(db, "   ", "longenough")
        assert auth.needs_setup(db)


def test_session_roundtrip_and_tamper():
    tok = auth.issue_session(SECRET, "alice")
    assert auth.read_session(SECRET, tok) == "alice"
    assert auth.read_session(SECRET, tok + "x") is None
    assert auth.read_session("other-secret", tok) is None
    assert auth.read_session(SECRET, "garbage") is None
    assert auth.read_session(SECRET, tok, max_age=-1) is None


def test_dependency_session_cookie(dbf):
    c = make_client(FakeStore(), dbf)
    assert c.get("/p").status_code == 401
    tok = auth.issue_session(SECRET, "alice")
    # no user yet -> 401 even with a validly signed cookie
    c.cookies.set(auth.SESSION_COOKIE, tok)
    assert c.get("/p").status_code == 401
    with_user(dbf)
    r = c.get("/p")
    assert r.status_code == 200 and r.json() == {"principal": "alice"}
    # session for a user that doesn't exist is rejected
    ghost = auth.issue_session(SECRET, "mallory")
    c.cookies.set(auth.SESSION_COOKIE, ghost)
    assert c.get("/p").status_code == 401


def test_api_key_header(dbf):
    with_user(dbf)
    c = make_client(FakeStore(), dbf)
    r = c.get("/p", headers={"X-Api-Key": API_KEY})
    assert r.status_code == 200 and r.json() == {"principal": "api-key"}
    assert c.get("/p", headers={"X-Api-Key": "nope"}).status_code == 401
    assert c.get("/p", params={"apikey": API_KEY}).status_code == 401


def test_setup_pending_rejects_even_api_key_and_local(dbf):
    c = make_client(FakeStore(bypass=True), dbf, peer=("192.168.1.2", 1))
    assert c.get("/p", headers={"X-Api-Key": API_KEY}).status_code == 401
    assert c.get("/p").status_code == 401


def test_local_bypass_ignores_forwarded_for(dbf):
    with_user(dbf)
    c = make_client(FakeStore(bypass=True), dbf, peer=("203.0.113.5", 1234))
    r = c.get("/p", headers={"X-Forwarded-For": "192.168.1.2", "X-Real-IP": "127.0.0.1",
                             "Forwarded": "for=192.168.1.2"})
    assert r.status_code == 401


def test_local_bypass_allows_private_peer(dbf):
    with_user(dbf)
    store = FakeStore(bypass=True)
    c = make_client(store, dbf, peer=("192.168.1.2", 1234))
    r = c.get("/p")
    assert r.status_code == 200 and r.json() == {"principal": "local"}
    store.current = AppConfig.model_validate({"auth": {"local_bypass": False}})  # hot reload
    assert c.get("/p").status_code == 401


@pytest.mark.parametrize("host,expected", [
    ("127.0.0.1", True), ("::1", True), ("10.1.2.3", True), ("172.16.0.1", True),
    ("192.168.1.2", True), ("169.254.1.1", True), ("fe80::1", True),
    ("::ffff:192.168.1.2", True), ("::ffff:203.0.113.5", False),
    ("203.0.113.5", False), ("8.8.8.8", False), ("testclient", False), ("", False),
])
def test_is_local(host, expected):
    class Req:
        class client:  # noqa: N801
            pass
    Req.client.host = host
    assert auth.is_local(Req) is expected


def test_is_local_no_client():
    class Req:
        client = None
    assert auth.is_local(Req) is False


def test_setup_closed_after_user_exists(dbf):
    with dbf() as db:
        auth.require_setup_open(db)  # no raise
        auth.create_user(db, "alice", "hunter2hunter2")
        with pytest.raises(HTTPException) as e:
            auth.require_setup_open(db)
        assert e.value.status_code == 403
        assert e.value.detail == "Setup already completed"
