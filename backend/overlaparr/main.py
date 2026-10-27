"""App factory: wires config, logging, database, scan engine, scheduler and the API."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from overlaparr import paths
from overlaparr.actions.tags import sync_tags
from overlaparr.api import (
    actions,
    activity,
    dashboard,
    findings,
    instances,
    plex,
    scan,
    servers,
    settings,
    setup,
)
from overlaparr.api.deps import require_auth
from overlaparr.arr.client import ArrClient
from overlaparr.arr.snapshot import make_client
from overlaparr.auth import make_require_auth
from overlaparr.automation.runner import run_rules
from overlaparr.config.schema import AppConfig, InstanceConfig
from overlaparr.config.store import ConfigStore
from overlaparr.config.watcher import start_watching
from overlaparr.db.session import init_db, make_engine, make_sessionmaker
from overlaparr.logging_setup import configure_logging, install_secret_redaction, redact
from overlaparr.loss import handle_losses
from overlaparr.notify.events import dispatch_after_scan
from overlaparr.plex.gateway import PlexApiGateway, PlexGateway
from overlaparr.scan.engine import ScanEngine
from overlaparr.scan.progress import ProgressBus
from overlaparr.scheduler import AppScheduler

log = logging.getLogger(__name__)

API_PREFIX = "/api/v1"
STATIC_DIR = Path(__file__).parent / "static"


def _files(config_dir: Path | None) -> tuple[Path, Path, Path, Path]:
    """(config.yaml, secrets.yaml, database, log file) for this config directory."""
    if config_dir is None:
        return paths.config_file(), paths.secrets_file(), paths.db_file(), paths.log_file()
    base = Path(config_dir)
    return (base / "config.yaml", base / "secrets.yaml", base / "overlaparr.db",
            base / "logs" / "overlaparr.log")


def _setup_logging(store: ConfigStore, log_path: Path) -> None:
    configure_logging(store.current.logging, log_path)
    install_secret_redaction(store)
    applied = [store.current.logging]

    def on_reload(cfg: AppConfig) -> None:
        if cfg.logging != applied[0]:
            applied[0] = cfg.logging
            configure_logging(cfg.logging, log_path)

    store.subscribe(on_reload)


def _redact_detail(detail: Any) -> Any:
    if isinstance(detail, str):
        return redact(detail)
    if isinstance(detail, list):
        return [_redact_detail(d) for d in detail]
    if isinstance(detail, dict):
        return {k: _redact_detail(v) for k, v in detail.items()}
    return detail


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse({"detail": _redact_detail(exc.detail)}, status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # Drop the submitted input from the errors: it may contain secrets.
        detail = [{"loc": [str(p) for p in e.get("loc", ())], "msg": redact(str(e.get("msg", "")))}
                  for e in exc.errors()]
        return JSONResponse({"detail": detail}, status_code=422)

    app.add_middleware(_UnhandledErrors)


class _UnhandledErrors:
    """Turn unhandled exceptions into a redacted 500.

    Done as middleware rather than an ``Exception`` handler: Starlette re-raises
    after such a handler, and the server would then log the raw, unredacted
    traceback through its own logger.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: dict) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:  # noqa: BLE001 - last line of defence
            log.error("unhandled error on %s %s", scope.get("method"), scope.get("path"),
                      exc_info=exc)
            if started:
                return  # response already under way; nothing more can be sent
            response = JSONResponse({"detail": redact(str(exc)) or "Internal server error"},
                                    status_code=500)
            await response(scope, receive, send)


def _mount_spa(app: FastAPI) -> None:
    """Serve the built frontend with history fallback; skipped when it isn't built."""
    root = STATIC_DIR.resolve()
    index = root / "index.html"
    if not index.is_file():
        return
    if (root / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path == "health" or full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(index)


def create_app(
    config_dir: Path | None = None,
    gateway_factory: Callable[[str], PlexGateway] | None = None,
    client_factory: Callable[[InstanceConfig], ArrClient] | None = None,
    start_background: bool = True,
) -> FastAPI:
    config_path, secrets_path, db_path, log_path = _files(config_dir)

    store = ConfigStore(config_path, secrets_path)
    store.load()
    _setup_logging(store, log_path)

    db_engine = make_engine(db_path)
    init_db(db_engine)
    db_factory = make_sessionmaker(db_engine)

    gateway_factory = gateway_factory or PlexApiGateway
    client_factory = client_factory or make_client
    bus = ProgressBus()
    scan_engine = ScanEngine(store, db_factory, gateway_factory, client_factory, bus)
    for hook in (sync_tags, handle_losses, run_rules, dispatch_after_scan):
        scan_engine.register_post_hook(hook)
    scheduler = AppScheduler(store, scan_engine, db_factory)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        stop_watching = None
        if start_background:
            stop_watching = start_watching(store)
            scheduler.start()
        try:
            yield
        finally:
            if stop_watching is not None:
                stop_watching()
            scheduler.shutdown()
            db_engine.dispose()

    app = FastAPI(title="Overlaparr", lifespan=lifespan)
    app.state.store = store
    app.state.db_engine = db_engine
    app.state.db_factory = db_factory
    app.state.gateway_factory = gateway_factory
    app.state.client_factory = client_factory
    app.state.bus = bus
    app.state.scan_engine = scan_engine
    app.state.scheduler = scheduler
    app.state.require_auth = make_require_auth(store, db_factory)
    app.state.log_path = log_path

    _install_error_handlers(app)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    api = APIRouter(prefix=API_PREFIX)
    api.include_router(setup.setup_router)
    api.include_router(setup.auth_router)
    protected = [Depends(require_auth)]
    for module in (plex, servers, instances, settings, scan, findings, actions, activity,
                   dashboard):
        api.include_router(module.router, dependencies=protected)
    app.include_router(api)

    _mount_spa(app)
    return app


def app_factory() -> FastAPI:
    """uvicorn entry point: ``uvicorn overlaparr.main:app_factory --factory``."""
    return create_app()
