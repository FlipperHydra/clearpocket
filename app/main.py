"""App factory. Run:  uvicorn app.main:create_app --factory --port 8000"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Settings, get_settings
from .db import make_engine
from .errors import ApiError
from .routers import auth, budget, transactions

log = logging.getLogger("clearpocket")

STATIC = {
    "/": ("clearpocket-budget.html", "text/html; charset=utf-8"),
    "/clearpocket-budget.html": ("clearpocket-budget.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


def _error(status: int, code: str, message: str, details=None, headers=None) -> JSONResponse:
    body = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return JSONResponse(body, status_code=status, headers=headers)


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for warning in settings.warnings():
        log.warning(warning)
    app = FastAPI(
        title="ClearPocket API",
        docs_url="/api/docs" if settings.api_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.api_docs else None,
    )
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return _error(exc.status, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        # Report field + message only; never echo submitted values (passwords, etc.).
        details = [
            {"field": ".".join(str(p) for p in e["loc"] if p not in ("body", "query", "path")), "message": e["msg"].removeprefix("Value error, ")}
            for e in exc.errors()
        ]
        return _error(422, "validation_error", "The request was invalid.", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _error(exc.status_code, code, "Not found." if exc.status_code == 404 else "Request failed.")

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        log.error("unhandled %s on %s %s", type(exc).__name__, request.method, request.url.path)
        return _error(500, "internal_error", "Something went wrong.")

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health", tags=["meta"])
    def health():
        with app.state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    app.include_router(auth.router)
    app.include_router(budget.router)
    app.include_router(transactions.router)

    def _static_route(path: str, filename: str, media: str):
        def handler():
            file = settings.frontend_dir / filename
            if not file.is_file():
                return _error(404, "not_found", "Not found.")
            return FileResponse(file, media_type=media)

        app.add_api_route(path, handler, methods=["GET"], include_in_schema=False)

    for path, (filename, media) in STATIC.items():  # explicit allow-list; no directory exposure
        _static_route(path, filename, media)

    @app.on_event("shutdown")
    def _shutdown():
        app.state.engine.dispose()

    return app
