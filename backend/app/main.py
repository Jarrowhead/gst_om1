"""FastAPI app factory + auth router wiring + error envelope handlers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.errors import (
    ServiceError,
    auth_error_handler,
    http_exception_handler,
    service_error_handler,
    validation_error_handler,
    value_error_handler,
)
from app.api.routers.auth import router as auth_router
from app.api.routers.bulk_import import router as bulk_import_router
from app.api.routers.documents import router as documents_router
from app.api.routers.dpdp import router as dpdp_router
from app.api.routers.einvoice import router as einvoice_router
from app.api.routers.gst_accounts import router as gst_accounts_router
from app.api.routers.itc import router as itc_router
from app.api.routers.notifications import router as notifications_router
from app.api.routers.profile import router as profile_router
from app.api.routers.returns import router as returns_router
from app.api.routers.settings import router as settings_router
from app.core.auth.errors import AuthError

Handler = Callable[[Request, Exception], Awaitable[JSONResponse]]


def create_app() -> FastAPI:
    app = FastAPI(title="GST Filing Platform API", version="0.1.0")
    routers = (
        auth_router,
        profile_router,
        settings_router,
        gst_accounts_router,
        documents_router,
        bulk_import_router,
        itc_router,
        returns_router,
        einvoice_router,
        notifications_router,
        dpdp_router,
    )
    for r in routers:
        app.include_router(r, prefix="/api/v1")

    # Cast-free handler registration: the handlers match Starlette's expected
    # signature exactly (Request, Exc) -> Awaitable[Response].
    auth_handler: Handler = auth_error_handler  # type: ignore[assignment]
    validation_handler: Handler = validation_error_handler  # type: ignore[assignment]
    service_handler: Handler = service_error_handler  # type: ignore[assignment]
    http_handler: Handler = http_exception_handler  # type: ignore[assignment]
    value_handler: Handler = value_error_handler  # type: ignore[assignment]
    app.add_exception_handler(AuthError, auth_handler)
    app.add_exception_handler(RequestValidationError, validation_handler)
    app.add_exception_handler(ServiceError, service_handler)
    app.add_exception_handler(HTTPException, http_handler)
    app.add_exception_handler(ValueError, value_handler)

    @app.get("/api/v1/health")
    async def health() -> dict[str, object]:
        return {"success": True, "data": {"status": "ok"}}

    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        _ = request, exc
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": {"code": "INTERNAL_ERROR", "message": "internal server error"},
            },
        )

    app.add_exception_handler(Exception, unhandled)

    return app


app = create_app()
