"""Single-user authentication: bcrypt passwords, signed session cookies, API key.

Functions take explicit inputs; ``make_require_auth`` builds the FastAPI
dependency that the app factory wires up.
"""

import hashlib
import hmac
import ipaddress
from collections.abc import Callable

import bcrypt
from fastapi import HTTPException, Request, Response
from itsdangerous import BadSignature, TimestampSigner
from sqlalchemy import select
from sqlalchemy.orm import Session

SESSION_COOKIE = "overlaparr_session"
SESSION_MAX_AGE = 30 * 24 * 3600
API_KEY_HEADER = "X-Api-Key"
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_BYTES = 72  # bcrypt ignores everything past 72 bytes
SESSION_SALT = "overlaparr.session"

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


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


# Checked against when the user is unknown so timing doesn't reveal usernames.
_DUMMY_HASH = _hash_password("overlaparr-dummy-password")


def create_user(db: Session, username: str, password: str) -> None:
    username = (username or "").strip()
    if not username:
        raise ValueError("username must not be empty")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(f"password must be at most {MAX_PASSWORD_BYTES} bytes (UTF-8)")
    if not needs_setup(db):
        raise ValueError("a user already exists")
    db.add(_user_model()(username=username, password_hash=_hash_password(password)))
    db.commit()


def verify(db: Session, username: str, password: str) -> bool:
    if not isinstance(password, str):
        return False
    user = db.scalars(select(_user_model()).where(_user_model().username == username)).first()
    hashed = user.password_hash if user is not None else _DUMMY_HASH
    try:
        ok = bcrypt.checkpw(password.encode("utf-8"), hashed.encode("ascii"))
    except ValueError:
        return False
    return ok and user is not None


def _hash_tag(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()[:16]


def issue_session(secret: str, username: str, password_hash: str) -> str:
    """Signed token binding the session to the user's current password hash."""
    if not secret:
        raise ValueError("session secret must not be empty")
    payload = f"{username}:{_hash_tag(password_hash)}"
    return TimestampSigner(secret, salt=SESSION_SALT).sign(payload).decode("utf-8")


def read_session(
    secret: str, token: str, max_age: int = SESSION_MAX_AGE
) -> tuple[str, str] | None:
    """Return ``(username, password_hash_tag)`` for a valid token, else None."""
    if not secret:
        return None
    try:
        payload = TimestampSigner(secret, salt=SESSION_SALT).unsign(token, max_age=max_age)
        username, tag = payload.decode("utf-8").rsplit(":", 1)
    except (BadSignature, UnicodeDecodeError, ValueError):
        return None
    return username, tag


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_MAX_AGE, path="/", httponly=True, samesite="lax"
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="lax")


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
                parsed = read_session(secret, token)
                if parsed is not None:
                    username, tag = parsed
                    user = db.scalars(
                        select(_user_model()).where(_user_model().username == username)
                    ).first()
                    if user is not None and hmac.compare_digest(
                        tag, _hash_tag(user.password_hash)
                    ):
                        return username
            if store.current.auth.local_bypass and is_local(request):
                return "local"
        raise HTTPException(status_code=401, detail="Not authenticated")

    return require_auth
