"""Hot-reload config.yaml/secrets.yaml when they are edited outside the app."""

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from watchfiles import watch

from overlaparr.config.store import ConfigStore

log = logging.getLogger(__name__)


def start_watching(store: ConfigStore) -> Callable[[], None]:
    """Watch the config directory in a daemon thread; returns a stop function.

    Events caused by the store's own writes are ignored: reload_from_disk only
    acts when the files' content hash differs from what the store last
    read or wrote.
    """
    names = {store.config_path.name, store.secrets_path.name}
    stop_event = threading.Event()

    def run() -> None:
        for _changes in watch(
            store.config_path.parent,
            watch_filter=lambda _change, path: Path(path).name in names,
            stop_event=stop_event,
            raise_interrupt=False,
        ):
            try:
                store.reload_from_disk()
            except Exception:
                log.exception("config reload failed")

    thread = threading.Thread(target=run, name="config-watcher", daemon=True)
    thread.start()

    def stop() -> None:
        stop_event.set()
        thread.join(timeout=5)

    return stop
