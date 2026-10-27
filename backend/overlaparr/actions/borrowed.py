"""Lend scan-owned *arr clients to code that closes the clients it is given."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from overlaparr.arr.snapshot import make_client
from overlaparr.config.schema import InstanceConfig


class Borrowed:
    """A scan-owned client lent to ``execute``/``undo``, which close clients when done."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def close(self) -> None:  # the scan engine closes the real client
        pass


def client_for(clients: dict[str, Any]) -> Callable:
    def get(inst: InstanceConfig, dry_run: bool = False) -> Any:
        if dry_run or inst.id not in clients:
            return make_client(inst, dry_run=dry_run)
        return Borrowed(clients[inst.id])

    return get
