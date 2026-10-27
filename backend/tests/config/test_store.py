import stat
import sys
import time

import pytest

from overlaparr.config.schema import InstanceConfig
from overlaparr.config.store import (
    MASK,
    ConfigInvalidOnDisk,
    ConfigStore,
    mask_secrets,
)
from overlaparr.config.watcher import start_watching


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "config.yaml", tmp_path / "secrets.yaml"


def make_store(paths, config_text=None, secrets_text=None):
    cfg, sec = paths
    if config_text is not None:
        cfg.write_text(config_text, encoding="utf-8")
    if secrets_text is not None:
        sec.write_text(secrets_text, encoding="utf-8")
    store = ConfigStore(cfg, sec)
    store.load()
    return store


def test_first_load_creates_files(paths):
    store = make_store(paths)
    cfg, sec = paths
    assert cfg.exists() and sec.exists()
    if sys.platform != "win32":
        assert stat.S_IMODE(sec.stat().st_mode) == 0o600
    assert store.get_secret("session_secret")
    assert store.get_secret("app_api_key")
    assert store.error is None
    # second load reuses the generated secrets
    again = ConfigStore(cfg, sec)
    again.load()
    assert again.get_secret("session_secret") == store.get_secret("session_secret")


def test_secret_tag_resolves(paths):
    store = make_store(
        paths, "plex:\n  token: !secret plex_token\n", "plex_token: abc\n"
    )
    assert store.current.plex.token == "abc"
    assert "abc" not in paths[0].read_text(encoding="utf-8")
    assert "abc" in store.secret_values()


def test_env_override(paths, monkeypatch):
    monkeypatch.setenv("RADARR_KEY", "envkey")
    text = (
        "instances:\n"
        "  - id: r1\n"
        "    name: Radarr\n"
        "    type: radarr\n"
        "    url: http://radarr:7878\n"
        "    api_key: ${RADARR_KEY}\n"
    )
    store = make_store(paths, text)
    assert store.current.instances[0].api_key == "envkey"
    store.update(lambda c: setattr(c.scan, "page_size", 100))
    written = paths[0].read_text(encoding="utf-8")
    assert "${RADARR_KEY}" in written
    assert "envkey" not in written


def test_update_preserves_comments(paths):
    store = make_store(paths, "# my comment\nscan:\n  stale_after_hours: 12\n")
    store.update(lambda c: setattr(c.scan, "stale_after_hours", 24))
    written = paths[0].read_text(encoding="utf-8")
    assert "# my comment" in written
    assert "stale_after_hours: 24" in written
    bak = paths[0].with_name("config.yaml.bak").read_text(encoding="utf-8")
    assert "stale_after_hours: 12" in bak
    assert store.current.scan.stale_after_hours == 24


def test_ui_entered_secret_goes_to_secrets_file(paths):
    store = make_store(paths)
    inst = InstanceConfig(
        id="abc123", name="Sonarr", type="sonarr", url="http://s:8989", api_key="k123"
    )
    store.update(lambda c: c.instances.append(inst))
    assert "!secret instance_abc123_api_key" in paths[0].read_text(encoding="utf-8")
    assert "k123" not in paths[0].read_text(encoding="utf-8")
    assert "k123" in paths[1].read_text(encoding="utf-8")
    assert store.get_secret("instance_abc123_api_key") == "k123"
    # reload from disk resolves it back
    fresh = ConfigStore(*paths)
    assert fresh.load().instances[0].api_key == "k123"


def test_changed_secret_updates_secrets_file_only(paths):
    store = make_store(
        paths, "plex:\n  token: !secret plex_token\n", "plex_token: abc\n"
    )
    store.update(lambda c: setattr(c.plex, "token", "xyz"))
    assert "token: !secret plex_token" in paths[0].read_text(encoding="utf-8")
    assert store.get_secret("plex_token") == "xyz"
    assert store.current.plex.token == "xyz"


def test_delete_secret(paths):
    store = make_store(paths, "plex:\n  token: !secret plex_token\n", "plex_token: abc\n")
    store.update(lambda c: setattr(c.plex, "token", None))
    store.delete_secret("plex_token")
    assert store.get_secret("plex_token") is None
    assert "plex_token" not in paths[1].read_text(encoding="utf-8")
    assert store.get_secret("session_secret")  # others untouched
    assert store.error is None
    store.delete_secret("plex_token")  # absent: no-op


def _break_disk(paths):
    paths[0].write_text('scan: {server_timeout_seconds: "x"}\n', encoding="utf-8")
    return paths[0].read_bytes()


def test_invalid_disk_edit_keeps_last_good(paths):
    store = make_store(paths, "scan:\n  server_timeout_seconds: 30\n")
    before = store.current
    broken = _break_disk(paths)
    assert store.reload_from_disk() is False
    assert store.current == before
    assert "server_timeout_seconds" in store.error
    assert paths[0].read_bytes() == broken


def test_update_refuses_while_disk_invalid(paths):
    store = make_store(paths)
    broken = _break_disk(paths)
    store.reload_from_disk()
    with pytest.raises(ConfigInvalidOnDisk):
        store.update(lambda c: setattr(c.scan, "page_size", 10))
    assert paths[0].read_bytes() == broken


def test_valid_disk_edit_applies_and_notifies(paths):
    store = make_store(paths)
    seen = []
    store.subscribe(seen.append)
    paths[0].write_text("scan:\n  page_size: 42\n", encoding="utf-8")
    assert store.reload_from_disk() is True
    assert store.current.scan.page_size == 42
    assert store.error is None
    assert [c.scan.page_size for c in seen] == [42]
    # nothing changed on disk since -> not re-applied
    assert store.reload_from_disk() is False


def test_missing_ids_generated_and_written_back(paths):
    store = make_store(paths, "rules:\n  - name: My rule\n")
    rid = store.current.rules[0].id
    assert len(rid) == 12
    assert f"id: {rid}" in paths[0].read_text(encoding="utf-8")


def test_mask_secrets(paths):
    text = (
        "plex:\n  token: tok\n  home_server: home\n"
        "instances:\n"
        "  - {id: s1, name: S, type: sonarr, url: 'http://s:8989', api_key: key1}\n"
        "notifications:\n  connections:\n"
        "    - {id: n1, name: A, type: apprise, urls: ['a://1', 'b://2']}\n"
        "    - {id: n2, name: W, type: webhook, url: 'http://hook'}\n"
    )
    store = make_store(paths, text)
    out = mask_secrets(store.current)
    assert out["plex"]["token"] == MASK
    assert out["plex"]["home_server"] == "home"
    assert out["instances"][0]["api_key"] == MASK
    assert out["instances"][0]["url"] == "http://s:8989"
    assert out["notifications"]["connections"][0]["urls"] == [MASK, MASK]
    assert out["notifications"]["connections"][1]["url"] == MASK
    assert str(out).count(MASK) == 5


def test_own_write_not_seen_as_disk_change(paths):
    store = make_store(paths)
    store.update(lambda c: setattr(c.scan, "page_size", 10))
    store.set_secret("other", "v")
    assert store.changed_on_disk() is False
    paths[0].write_text("scan:\n  page_size: 7\n", encoding="utf-8")
    assert store.changed_on_disk() is True


def test_watcher_applies_external_edit(paths):
    store = make_store(paths)
    stop = start_watching(store)
    try:
        time.sleep(0.3)
        paths[0].write_text("scan:\n  page_size: 77\n", encoding="utf-8")
        deadline = time.monotonic() + 10
        while store.current.scan.page_size != 77 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert store.current.scan.page_size == 77
    finally:
        stop()


def test_set_secret_refreshes_current(paths):
    store = make_store(
        paths, "plex:\n  token: !secret plex_token\n", "plex_token: old\n"
    )
    seen = []
    store.subscribe(seen.append)
    store.set_secret("plex_token", "new")
    assert store.current.plex.token == "new"
    assert [c.plex.token for c in seen] == ["new"]
    # an unreferenced secret does not trigger a notification
    store.set_secret("unrelated", "x")
    assert len(seen) == 1
