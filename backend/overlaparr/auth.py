"""Single-user authentication: bcrypt passwords, signed session cookies, API key.

Functions take explicit inputs; ``make_require_auth`` builds the FastAPI
dependency that the app factory wires up.
"""

import hmac
import ipaddress
from collections.abc import Callable

import bcrypt
from fastapi import HTTPException, Request
from itsdangerous import BadSignature, TimestampSigner
from sqlalchemy import select
from sqlalchemy.orm import Session

SESSION_COOKIE = "overlaparr_session"
SESSION_MAX_AGE = 30 * 24 * 3600
API_KEY_HEADER = "X-Api-Key"
MIN_PASSWORD_LENGTH = 8

# Explicit list: ``ip.is_private`` also covers documentation ranges (TEST-NET,
# 203.0.113.0/24 etc.), which must not count as local.
_LOCAL_NETS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
        "::1/128", "fc00::/7", "fe80::/10",
    )
)


def _user_model():
    from overlaparr.db.models import User

    return User


def needs_setup(db: Session) -> bool:
    return db.scalars(select(_user_model()).limit(1)).first() is None


def require_setup_open(db: Session) -> None:
    if not needs_setup(db):
        raise HTTPException(status_code=403, detail="Setup already completed")


def create_user(db: Session, username: str, password: str) -> None:
    username = (username or "").strip()
    if not username:
        raise ValueError("username must not be empty")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if not needs_setup(db):
        raise ValueError("a user already exists")
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    db.add(_user_model()(username=username, password_hash=hashed))
    db.commit()


def verify(db: Session, username: str, password: str) -> bool:
    user = db.scalars(select(_user_model()).where(_user_model().username == username)).first()
    if user is None:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), user.password_hash.encode("ascii"))
    except ValueError:
        return False


def issue_session(secret: str, username: str) -> str:
    return TimestampSigner(secret).sign(username).decode("utf-8")


def read_session(secret: str, token: str, max_age: int = SESSION_MAX_AGE) -> str | None:
    try:
        return TimestampSigner(secret).unsign(token, max_age=max_age).decode("utf-8")
    except (BadSignature, UnicodeDecodeError):
        return None


def is_local(request: Request) -> bool:
    """True when the direct TCP peer is private/loopback/link-local.

    Deliberately ignores X-Forwarded-For and friends: they are client-controlled.
    """
    client = request.client
    if client is None or not client.host:
        return False
    try:
        ip = ipaddress.ip_address(client.host)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip in net for net in _LOCAL_NETS)


def make_require_auth(store, db_factory) -> Callable[[Request], str]:
    """Build a dependency returning the principal: username, "api-key" or "local"."""

    def require_auth(request: Request) -> str:
        with db_factory() as db:
            if needs_setup(db):
                raise HTTPException(status_code=401, detail="Setup required")
            key = store.get_secret("app_api_key")
            supplied = request.headers.get(API_KEY_HEADER)
            if key and supplied and hmac.compare_digest(supplied.encode(), key.encode()):
                return "api-key"
            secret = store.get_secret("session_secret")
            token = request.cookies.get(SESSION_COOKIE)
            if secret and token:
                username = read_session(secret, token)
                if username is not None:
                    exists = db.scalars(
                        select(_user_model()).where(_user_model().username == username)
                    ).first()
                    if exists is not None:
                        return username
            if store.current.auth.local_bypass and is_local(request):
                return "local"
        raise HTTPException(status_code=401, detail="Not authenticated")

    return require_auth
