"""Application logs: a filtered tail of the JSON log file, and a zip download."""

from __future__ import annotations

import io
import json
import os
import zipfile
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Query, Request, Response

router = APIRouter(prefix="/logs", tags=["logs"])

LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
BLOCK = 1 << 16


def _log_path(request: Request) -> Path:
    return Path(request.app.state.log_path)


def _rotated(path: Path) -> list[Path]:
    """path.1, path.2, ... that exist, oldest last."""
    prefix = path.name + "."
    found = [p for p in path.parent.glob(prefix + "*") if p.name[len(prefix):].isdigit()]
    return sorted(found, key=lambda p: int(p.name[len(prefix):]))


def _reverse_lines(path: Path, block: int = BLOCK) -> Iterator[str]:
    """Non-empty lines of ``path``, last first, reading backwards one block at a time."""
    try:
        f = open(path, "rb")  # noqa: SIM115 - closed below, generator may stop early
    except FileNotFoundError:
        return
    with f:
        pos = f.seek(0, os.SEEK_END)
        rest = b""
        while pos > 0:
            step = min(block, pos)
            pos -= step
            f.seek(pos)
            lines = (f.read(step) + rest).split(b"\n")
            rest = lines.pop(0)  # may continue in the previous block
            for line in reversed(lines):
                if line.strip():
                    yield line.decode("utf-8", "replace")
        if rest.strip():
            yield rest.decode("utf-8", "replace")


def _entries(paths: list[Path]) -> Iterator[dict]:
    for path in paths:
        with closing(_reverse_lines(path)) as lines:
            for line in lines:
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if isinstance(data, dict):
                    yield data


@router.get("")
def tail_logs(request: Request,
              level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO",
              limit: int = Query(500, ge=1, le=2000)):
    path = _log_path(request)
    minimum = LEVELS[level]
    out: list[dict] = []
    with closing(_entries([path, path.with_name(path.name + ".1")])) as entries:
        for data in entries:
            if LEVELS.get(str(data.get("level")), 0) < minimum:
                continue
            out.append({k: data.get(k) for k in ("ts", "level", "logger", "msg", "exc")})
            if len(out) >= limit:
                break
    return out


@router.get("/download")
def download_logs(request: Request):
    path = _log_path(request)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in [path, *_rotated(path)]:
            try:
                z.write(p, arcname=p.name)
            except FileNotFoundError:
                continue
    return Response(buf.getvalue(), media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="overlaparr-logs.zip"'})
