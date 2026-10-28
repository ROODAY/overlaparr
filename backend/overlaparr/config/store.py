"""YAML-backed config store.

``config.yaml`` is the user-editable source of truth; secret values live in
``secrets.yaml`` and are referenced from config.yaml as ``!secret <key>``.
Any scalar may be ``${ENV_VAR}``: the environment value wins when set, and the
reference is written back unchanged. Saves round-trip the existing YAML so the
user's comments, ordering and formatting survive UI edits.
"""

import hashlib
import io
import logging
import os
import re
import secrets as _secrets
import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq, TaggedScalar
from ruamel.yaml.error import YAMLError

from overlaparr.config.schema import AppConfig, new_id

log = logging.getLogger(__name__)

MASK = "********"
SECRET_TAG = "!secret"
GENERATED_SECRETS = ("session_secret", "app_api_key")

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
NOTIFY_SECRET_FIELDS = ("password", "webhook_url", "url", "urls")
_ID_LISTS = (("rules",), ("instances",), ("notifications", "connections"))
_HEADER = (
    "Overlaparr configuration.\n"
    "Secrets live in secrets.yaml and are referenced here as: !secret <key>\n"
    "Any value may be written as ${ENV_VAR}; the environment wins when set."
)


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING: Any = _Missing()


class ConfigInvalidOnDisk(Exception):
    """config.yaml/secrets.yaml on disk cannot be parsed or validated."""


def _yaml() -> YAML:
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def instance_secret_key(instance_id: str) -> str:
    return f"instance_{instance_id}_api_key"


def notify_secret_key(conn_id: str, field: str) -> str:
    return f"notify_{conn_id}_{field}"


def _secret_key(path: tuple) -> str | None:
    """Secret key for a field path (list items are addressed by their id)."""
    if path == ("plex", "token"):
        return "plex_token"
    if len(path) == 3 and path[0] == "instances" and path[2] == "api_key":
        return instance_secret_key(path[1])
    if (
        len(path) == 4
        and path[:2] == ("notifications", "connections")
        and path[3] in NOTIFY_SECRET_FIELDS
    ):
        return notify_secret_key(path[2], path[3])
    return None


def _item_key(item: Any, index: int) -> Any:
    if isinstance(item, dict) and "id" in item:
        return str(item["id"])
    return index


def _secret_ref(node: Any) -> str | None:
    if isinstance(node, TaggedScalar) and str(node.tag) == SECRET_TAG:
        return str(node.value)
    return None


def _resolve_env(value: Any) -> Any:
    if isinstance(value, str):
        m = _ENV_RE.fullmatch(value)
        if m and m.group(1) in os.environ:
            return os.environ[m.group(1)]
        return str(value)
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    return value


def _resolve(node: Any, secrets: dict) -> Any:
    """Convert a round-trip YAML tree to plain data, resolving tags and env."""
    if isinstance(node, dict):
        return {str(k): _resolve(v, secrets) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(v, secrets) for v in node]
    key = _secret_ref(node)
    if key is not None:
        if key not in secrets:
            raise ConfigInvalidOnDisk(
                f"secret {key!r} referenced in config.yaml is missing from secrets.yaml"
            )
        return _resolve_env(_plain(secrets[key]))
    if isinstance(node, TaggedScalar):
        return _resolve_env(node.value)
    return _resolve_env(node)


def _plain(node: Any) -> Any:
    if isinstance(node, dict):
        return {str(k): _plain(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_plain(v) for v in node]
    if isinstance(node, str):
        return str(node)
    return node


def _map_secret_fields(data: Any, fn: Callable[[Any], Any], path: tuple = ()) -> Any:
    """Copy of plain ``data`` with every non-null secret field replaced by fn(value)."""
    if _secret_key(path) is not None and data is not None:
        return fn(data)
    if isinstance(data, dict):
        return {k: _map_secret_fields(v, fn, path + (k,)) for k, v in data.items()}
    if isinstance(data, list):
        return [
            _map_secret_fields(v, fn, path + (_item_key(v, i),))
            for i, v in enumerate(data)
        ]
    return data


def mask_secrets(cfg: AppConfig) -> dict:
    """JSON-ready config dict with secret values replaced by MASK (for API output)."""

    def mask(v: Any) -> Any:
        return [MASK] * len(v) if isinstance(v, list) else MASK

    return _map_secret_fields(cfg.model_dump(mode="json"), mask)


def _merge(node: Any, old: Any, new: Any, path: tuple, secrets_out: dict) -> Any:
    """Return the YAML node representing ``new``, reusing ``node`` where possible.

    ``old`` is the resolved value ``node`` currently stands for; unchanged values
    keep their original node (so ``${ENV}``/``!secret`` references and comments
    survive). Changed secret fields are emitted as ``!secret`` tags and their
    values collected in ``secrets_out``.
    """
    key = _secret_ref(node) or _secret_key(path)
    if key is not None and new is not None:
        if node is not MISSING and old == new:
            return node
        secrets_out[key] = new
        return TaggedScalar(key, tag=SECRET_TAG)
    if node is not MISSING and old == new:
        return node
    if isinstance(new, dict):
        m = node if isinstance(node, CommentedMap) else CommentedMap()
        old_map = old if isinstance(old, dict) else {}
        for k, v in new.items():
            child = m.get(k, MISSING)
            prev = old_map.get(k, MISSING)
            if child is MISSING and prev == v:
                continue  # absent from the file and unchanged: leave it implicit
            m[k] = _merge(child, prev, v, path + (k,), secrets_out)
        for k in [k for k in m if k not in new]:
            del m[k]
        return m
    if isinstance(new, list):
        return _merge_list(node, old, new, path, secrets_out)
    return new


def _merge_list(node: Any, old: Any, new: list, path: tuple, secrets_out: dict) -> Any:
    keyed = all(isinstance(i, dict) and "id" in i for i in new)
    if not keyed or not isinstance(node, CommentedSeq):
        seq = CommentedSeq()
        for i, item in enumerate(new):
            seq.append(_merge(MISSING, MISSING, item, path + (_item_key(item, i),), secrets_out))
        return seq
    existing = {
        str(n["id"]): n for n in node if isinstance(n, CommentedMap) and "id" in n
    }
    old_by_id = {
        str(o["id"]): o for o in (old or []) if isinstance(o, dict) and "id" in o
    }
    desired = []
    for item in new:
        iid = str(item["id"])
        desired.append(
            _merge(existing.get(iid, MISSING), old_by_id.get(iid, MISSING), item,
                   path + (iid,), secrets_out)
        )
    # Edit the sequence in place so surviving items keep their comments.
    keep = {id(d) for d in desired}
    for i in reversed(range(len(node))):
        if id(node[i]) not in keep:
            del node[i]
    for i, d in enumerate(desired):
        if i < len(node) and node[i] is d:
            continue
        for j in range(i, len(node)):
            if node[j] is d:
                del node[j]
                break
        node.insert(i, d)
    return node


def _ensure_ids(doc: CommentedMap) -> bool:
    added = False
    for path in _ID_LISTS:
        seq: Any = doc
        for part in path:
            seq = seq.get(part) if isinstance(seq, dict) else None
        if not isinstance(seq, list):
            continue
        for item in seq:
            if isinstance(item, CommentedMap) and not item.get("id"):
                if "id" in item:
                    del item["id"]
                item.insert(0, "id", new_id())
                added = True
    return added


def _digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def _atomic_write(path: Path, data: bytes, *, backup: bool = False, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    if mode is not None:
        os.chmod(tmp, mode)
    if backup and path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    os.replace(tmp, path)
    if mode is not None:
        os.chmod(path, mode)


class ConfigStore:
    def __init__(self, config_path: Path, secrets_path: Path):
        self.config_path = Path(config_path)
        self.secrets_path = Path(secrets_path)
        self._lock = threading.RLock()
        self._current: AppConfig | None = None
        self._error: str | None = None
        self._secrets = CommentedMap()
        self._known: tuple[str | None, str | None] = (None, None)
        self._subscribers: list[Callable[[AppConfig], None]] = []
        self._error_subscribers: list[Callable[[str], None]] = []

    # ----- public API -------------------------------------------------

    @property
    def current(self) -> AppConfig:
        if self._current is None:
            raise RuntimeError("ConfigStore.load() has not been called")
        return self._current

    @property
    def error(self) -> str | None:
        return self._error

    def load(self) -> AppConfig:
        """Read the files; an invalid config.yaml never stops the app.

        Without a previous good config the defaults are used and the problem is
        kept in ``error``. The broken file is left untouched and not remembered,
        so the next reload applies the user's fix.
        """
        with self._lock:
            try:
                _, cfg = self._read_disk()
            except ConfigInvalidOnDisk as e:
                self._error = str(e)
                self._known = (None, None)
                log.error("config on disk is invalid: %s", e)
                if self._current is None:
                    self._current = AppConfig()
                current, error = self._current, str(e)
            else:
                self._error = None
                self._current = cfg
                self._remember_disk()
                return cfg
        self._notify_error(error)  # outside the lock: subscribers may read the store
        return current

    def reload_from_disk(self) -> bool:
        """Re-read the files if they changed since we last read/wrote them.

        Returns True when a new valid config was applied. An invalid file keeps
        the last good config and records the problem in ``error``.
        """
        with self._lock:
            if not self.changed_on_disk():
                return False
            try:
                _, cfg = self._read_disk()
            except ConfigInvalidOnDisk as e:
                self._error = str(e)
                self._remember_disk()
                log.warning("config on disk is invalid, keeping last good config: %s", e)
                error = str(e)
            else:
                error = None
                self._error = None
                self._remember_disk()
                if cfg == self._current:
                    return False
                self._current = cfg
        if error is not None:
            self._notify_error(error)  # outside the lock: subscribers may read the store
            return False
        self._notify(cfg)
        return True

    def update(self, mutator: Callable[[AppConfig], None]) -> AppConfig:
        """Apply ``mutator`` to a copy of the config, validate, save, and swap.

        Always merges into what is on disk now; refuses (ConfigInvalidOnDisk)
        rather than overwrite a config file the user has broken.
        """
        with self._lock:
            try:
                doc, base = self._read_disk()
            except ConfigInvalidOnDisk as e:
                self._error = str(e)
                raise
            self._error = None
            draft = base.model_copy(deep=True)
            mutator(draft)
            new = AppConfig.model_validate(draft.model_dump())
            secret_writes: dict = {}
            doc = _merge(doc, base.model_dump(mode="json"), new.model_dump(mode="json"),
                         (), secret_writes)
            if secret_writes:
                for k, v in secret_writes.items():
                    self._secrets[k] = v
                self._write_secrets()
            self._write_config(doc)
            changed = new != self._current
            self._current = new
        if changed:
            self._notify(new)
        return new

    def get_secret(self, key: str) -> str | None:
        with self._lock:
            value = self._secrets.get(key)
            return None if value is None else _resolve_env(_plain(value))

    def set_secret(self, key: str, value: str) -> None:
        self._change_secrets(lambda secrets: secrets.__setitem__(key, value))

    def delete_secret(self, key: str) -> None:
        """Remove a secret; call once config.yaml no longer references it."""
        self._change_secrets(lambda secrets: secrets.pop(key, None))

    def _change_secrets(self, change: Callable[[CommentedMap], Any]) -> None:
        with self._lock:
            change(self._secrets)
            self._write_secrets()
            # config.yaml may reference this key: re-resolve so current isn't stale.
            try:
                _, cfg = self._read_disk()
            except ConfigInvalidOnDisk as e:
                self._error = str(e)
                return
            self._error = None
            self._remember_disk()
            if cfg == self._current:
                return
            self._current = cfg
        self._notify(cfg)

    def secret_values(self) -> list[str]:
        """Every resolved secret string (for log redaction)."""
        with self._lock:
            found: list[str] = []

            def collect(v: Any) -> Any:
                found.extend(v if isinstance(v, list) else [v])
                return v

            for v in self._secrets.values():
                collect(_resolve_env(_plain(v)))
            if self._current is not None:
                _map_secret_fields(self._current.model_dump(mode="json"), collect)
            return list(dict.fromkeys(s for s in found if isinstance(s, str) and s))

    def subscribe(self, cb: Callable[[AppConfig], None]) -> None:
        self._subscribers.append(cb)

    def subscribe_errors(self, cb: Callable[[str], None]) -> None:
        """``cb(message)`` whenever loading/reloading finds config.yaml invalid."""
        self._error_subscribers.append(cb)

    def changed_on_disk(self) -> bool:
        """True if either file differs from what the store last read or wrote."""
        return (_digest(self.config_path), _digest(self.secrets_path)) != self._known

    # ----- internals --------------------------------------------------

    def _notify_error(self, message: str) -> None:
        for cb in list(self._error_subscribers):
            try:
                cb(message)
            except Exception:
                log.exception("config error subscriber failed")

    def _notify(self, cfg: AppConfig) -> None:
        for cb in list(self._subscribers):
            try:
                cb(cfg)
            except Exception:
                log.exception("config subscriber failed")

    def _remember_disk(self) -> None:
        self._known = (_digest(self.config_path), _digest(self.secrets_path))

    def _read_disk(self) -> tuple[CommentedMap, AppConfig]:
        self._load_secrets()
        if not self.config_path.exists():
            doc = _merge(MISSING, MISSING, AppConfig().model_dump(mode="json"), (), {})
            doc.yaml_set_start_comment(_HEADER)
            self._write_config(doc)
        try:
            doc = _yaml().load(self.config_path.read_text(encoding="utf-8"))
        except YAMLError as e:
            raise ConfigInvalidOnDisk(f"config.yaml is not valid YAML: {e}") from e
        if doc is None:
            doc = CommentedMap()
        if not isinstance(doc, CommentedMap):
            raise ConfigInvalidOnDisk("config.yaml must contain a mapping at the top level")
        ids_added = _ensure_ids(doc)
        try:
            cfg = AppConfig.model_validate(_resolve(doc, self._secrets))
        except ValidationError as e:
            raise ConfigInvalidOnDisk(f"config.yaml is invalid: {e}") from e
        if ids_added:
            self._write_config(doc)
        return doc, cfg

    def _load_secrets(self) -> None:
        data = None
        if self.secrets_path.exists():
            try:
                data = _yaml().load(self.secrets_path.read_text(encoding="utf-8"))
            except YAMLError as e:
                raise ConfigInvalidOnDisk(f"secrets.yaml is not valid YAML: {e}") from e
            if data is not None and not isinstance(data, CommentedMap):
                raise ConfigInvalidOnDisk("secrets.yaml must contain a mapping")
        self._secrets = data if data is not None else CommentedMap()
        missing = [k for k in GENERATED_SECRETS if not self._secrets.get(k)]
        for k in missing:
            self._secrets[k] = _secrets.token_urlsafe(32)
        if missing or not self.secrets_path.exists():
            self._write_secrets()

    def _dump(self, doc: Any) -> bytes:
        buf = io.StringIO()
        _yaml().dump(doc, buf)
        return buf.getvalue().encode("utf-8")

    def _write_config(self, doc: CommentedMap) -> None:
        _atomic_write(self.config_path, self._dump(doc), backup=True)
        self._remember_disk()

    def _write_secrets(self) -> None:
        _atomic_write(self.secrets_path, self._dump(self._secrets), mode=0o600)
        self._remember_disk()
