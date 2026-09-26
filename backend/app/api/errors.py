"""API error envelope (API_SPECIFICATION.md conventions)."""

from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.auth.errors import AuthError


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
