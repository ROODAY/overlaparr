"""Scans: start one, read progress and history, stream progress as server-sent events."""

from __future__ import annotations

import asyncio
import json
import queue
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from overlaparr.api.deps import get_db, get_engine
from overlaparr.db.models import ScanRun
from overlaparr.scan.engine import ScanEngine
from overlaparr.scan.progress import ProgressBus

router = APIRouter(prefix="/scan", tags=["scan"])

RECENT_RUNS = 10
POLL_SECONDS = 0.25
KEEPALIVE_SECONDS = 15.0


def run_dict(run: ScanRun) -> dict:
    return {
        "id": run.id,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "status": run.status,
        "trigger": run.trigger,
        "error": run.error,
        "summary": run.summary,
    }


@router.post("", status_code=202)
def start_scan(engine: ScanEngine = Depends(get_engine)):
    scan_id = engine.start("manual")
    if scan_id is None:
        raise HTTPException(status_code=409, detail="Scan already running")
    return {"scan_id": scan_id}


@router.get("/status")
def scan_status(request: Request, db: Session = Depends(get_db)):
    runs = db.scalars(select(ScanRun).order_by(ScanRun.id.desc()).limit(RECENT_RUNS))
    return {"progress": request.app.state.bus.snapshot(), "runs": [run_dict(r) for r in runs]}


def _event(state: dict) -> str:
    return f"data: {json.dumps(state, default=str)}\n\n"


async def _stream(request: Request, bus: ProgressBus) -> AsyncIterator[str]:
    q = bus.subscribe()
    try:
        yield _event(bus.snapshot())
        last_sent = time.monotonic()
        while not await request.is_disconnected():
            try:
                state = q.get_nowait()
            except queue.Empty:
                if time.monotonic() - last_sent >= KEEPALIVE_SECONDS:
                    yield ": keepalive\n\n"
                    last_sent = time.monotonic()
                await asyncio.sleep(POLL_SECONDS)
                continue
            yield _event(state)
            last_sent = time.monotonic()
    finally:
        bus.unsubscribe(q)


@router.get("/events")
def scan_events(request: Request):
    return StreamingResponse(
        _stream(request, request.app.state.bus),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
