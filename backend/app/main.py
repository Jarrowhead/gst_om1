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
from app.api.routers.business import RegRouter
from app.api.routers.business import router as business_router
from app.core.auth.errors import AuthError

Handler = Callable[[Request, Exception], Awaitable[JSONResponse]]


def create_app() -> FastAPI:
    app = FastAPI(title="GST Filing Platform API", version="0.1.0")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(business_router, prefix="/api/v1")
    app.include_router(RegRouter, prefix="/api/v1")

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
