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
    def __init__(self, bypass=False, secrets=None):
        self.current = AppConfig.model_validate({"auth": {"local_bypass": bypass}})
        self.secrets = {"session_secret": SECRET, "app_api_key": API_KEY}
        self.secrets.update(secrets or {})

    def get_secret(self, key):
        return self.secrets.get(key)


def user_hash(dbf):
    with dbf() as db:
        return db.query(User).one().password_hash


def token_for(dbf, secret=SECRET):
    return auth.issue_session(secret, "alice", user_hash(dbf))


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
    tok = auth.issue_session(SECRET, "alice", "hash")
    name, h = auth.read_session(SECRET, tok)
    assert name == "alice" and len(h) == 16
    assert auth.read_session(SECRET, tok)[1] == auth.read_session(SECRET, tok)[1]
    assert auth.read_session(SECRET, auth.issue_session(SECRET, "a:b", "hash"))[0] == "a:b"
    assert auth.read_session(SECRET, tok + "x") is None
    assert auth.read_session("other-secret", tok) is None
    assert auth.read_session(SECRET, "garbage") is None
    assert auth.read_session(SECRET, tok, max_age=-1) is None
    assert auth.read_session("", tok) is None
    assert auth.read_session(None, tok) is None
    with pytest.raises(ValueError):
        auth.issue_session("", "alice", "hash")
    with pytest.raises(ValueError):
        auth.issue_session(None, "alice", "hash")
    # signed with the default (unsalted) signer -> rejected
    from itsdangerous import TimestampSigner
    raw = TimestampSigner(SECRET).sign("alice:" + "0" * 16).decode()
    assert auth.read_session(SECRET, raw) is None


def test_dependency_session_cookie(dbf):
    c = make_client(FakeStore(), dbf)
    assert c.get("/p").status_code == 401
    tok = auth.issue_session(SECRET, "alice", "whatever")
    # no user yet -> 401 even with a validly signed cookie
    c.cookies.set(auth.SESSION_COOKIE, tok)
    assert c.get("/p").status_code == 401
    with_user(dbf)
    tok = token_for(dbf)
    c.cookies.set(auth.SESSION_COOKIE, tok)
    r = c.get("/p")
    assert r.status_code == 200 and r.json() == {"principal": "alice"}
    # session for a user that doesn't exist is rejected
    ghost = auth.issue_session(SECRET, "mallory", user_hash(dbf))
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


@pytest.mark.parametrize("empty", [None, ""])
def test_empty_api_key_never_authenticates(dbf, empty):
    with_user(dbf)
    c = make_client(FakeStore(secrets={"app_api_key": empty}), dbf)
    assert c.get("/p").status_code == 401
    assert c.get("/p", headers={"X-Api-Key": ""}).status_code == 401


@pytest.mark.parametrize("empty", [None, ""])
def test_empty_session_secret_never_authenticates(dbf, empty):
    with_user(dbf)
    c = make_client(FakeStore(secrets={"session_secret": empty}), dbf)
    from itsdangerous import TimestampSigner
    forged = TimestampSigner("", salt="overlaparr.session").sign(
        "alice:" + auth._hash_tag(user_hash(dbf))).decode()
    c.cookies.set(auth.SESSION_COOKIE, forged)
    assert c.get("/p").status_code == 401


def test_session_revoked_by_password_change_or_recreate(dbf):
    with_user(dbf)
    c = make_client(FakeStore(), dbf)
    tok = token_for(dbf)
    c.cookies.set(auth.SESSION_COOKIE, tok)
    assert c.get("/p").status_code == 200
    with dbf() as db:  # password change
        u = db.query(User).one()
        u.password_hash = auth._hash_password("another-password")
        db.commit()
    assert c.get("/p").status_code == 401
    with dbf() as db:  # re-creation with the same name and password
        db.query(User).delete()
        db.commit()
        auth.create_user(db, "alice", "hunter2hunter2")
    assert c.get("/p").status_code == 401


def test_verify_unknown_user_runs_bcrypt(dbf, monkeypatch):
    calls = []
    real = auth.bcrypt.checkpw
    monkeypatch.setattr(auth.bcrypt, "checkpw", lambda *a: calls.append(a) or real(*a))
    with dbf() as db:
        assert auth.verify(db, "nobody", "whatever-pw") is False
        assert len(calls) == 1
        assert auth.verify(db, "nobody", None) is False
        auth.create_user(db, "alice", "hunter2hunter2")
        assert auth.verify(db, "alice", None) is False


def test_password_too_long_for_bcrypt(dbf):
    with dbf() as db:
        with pytest.raises(ValueError, match="72"):
            auth.create_user(db, "alice", "a" * 73)
        with pytest.raises(ValueError, match="72"):
            auth.create_user(db, "alice", "\u00e9" * 37)  # 74 bytes
        auth.create_user(db, "alice", "a" * 72)


def test_session_cookie_helpers():
    from fastapi import Response
    r = Response()
    auth.set_session_cookie(r, "tok")
    c = r.headers["set-cookie"].lower()
    assert "overlaparr_session=tok" in c and "httponly" in c and "samesite=lax" in c
    assert f"max-age={auth.SESSION_MAX_AGE}" in c and "path=/" in c
    r = Response()
    auth.clear_session_cookie(r)
    c = r.headers["set-cookie"].lower()
    assert "overlaparr_session=" in c and "max-age=0" in c
