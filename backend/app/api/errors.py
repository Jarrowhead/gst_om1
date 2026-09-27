"""API error envelope (API_SPECIFICATION.md conventions)."""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.auth.errors import AuthError


class ServiceError(Exception):
    """Domain-layer error with HTTP mapping (task 0.6 validation/filing)."""

    def __init__(self, message: str, status_code: int, code: str) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


def _envelope(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"success": False, "error": {"code": code, "message": message}},
    )


async def auth_error_handler(request: Request, exc: AuthError) -> JSONResponse:
    """Every AuthError becomes the doc's error envelope."""
    _ = request
    return _envelope(exc.code, exc.message, exc.status_code)


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """422 with a compact message (field list folded in)."""
    _ = request
    first = exc.errors()[0] if exc.errors() else {"loc": (), "msg": "invalid body"}
    loc = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
    return _envelope("VALIDATION_ERROR", f"{loc}: {first['msg']}", 422)


async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """Validation failures from domain helpers become 422 envelope."""
    _ = request
    return _envelope("VALIDATION_ERROR", str(exc), 422)


async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    """Domain errors (GSTIN/PAN validation, conflicts) become the API envelope."""
    _ = request
    return _envelope(exc.code, exc.message, exc.status_code)


_STATUS_CODES: dict[int, str] = {
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
}


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Any FastAPI HTTPException becomes the doc's error envelope."""
    _ = request
    return _envelope(
        _STATUS_CODES.get(exc.status_code, "INTERNAL_ERROR"),
        exc.detail,
        exc.status_code,
    )
