"""API error envelope (API_SPECIFICATION.md conventions)."""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.auth.errors import AuthError


class ServiceError(Exception):
    """Domain-layer error with HTTP mapping (task 0.6 validation/filing)."""

    def __init__(self, message: str, status_code: int, code: str) -> None:
        """Attach HTTP status and envelope code for service_error_handler.

        Flow:
            Stored fields are copied into {success:false, error:{code,message}}.

        Debug:
            409 CONFLICT on duplicate PAN/GSTIN is raised as ServiceError from businesses.service.
        """
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


def _envelope(code: str, message: str, status: int) -> JSONResponse:
    """Build the standard {success:false, error:{code,message}} JSON body.

    Debug:
        Clients branch on error.code, not on free-text message.
    """
    return JSONResponse(
        status_code=status,
        content={"success": False, "error": {"code": code, "message": message}},
    )


async def auth_error_handler(request: Request, exc: AuthError) -> JSONResponse:
    """Every AuthError becomes the doc's error envelope.

    Flow:
        Map exc.code / message / status_code through _envelope. Request unused.

    Debug:
        OTP, JWT, TOTP, and access 404s all land here because AccessDenied subclasses AuthError.
    """
    _ = request
    return _envelope(exc.code, exc.message, exc.status_code)


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """422 with a compact message (first Pydantic error only).

    Flow:
        Take errors()[0], join loc without 'body', prefix the message.

    Debug:
        Later field errors are dropped. Full list is on exc.errors() in a debugger.
    """
    _ = request
    first = exc.errors()[0] if exc.errors() else {"loc": (), "msg": "invalid body"}
    loc = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
    return _envelope("VALIDATION_ERROR", f"{loc}: {first['msg']}", 422)


async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """Validation failures from domain helpers become 422 envelope.

    Flow:
        str(exc) → VALIDATION_ERROR. Used for GSTIN/PAN ValueError if not wrapped.

    Debug:
        Service layer usually raises ServiceError instead; this is the fallback.
    """
    _ = request
    return _envelope("VALIDATION_ERROR", str(exc), 422)


async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    """Domain errors (conflicts, TOTP required) become the API envelope.

    Flow:
        _envelope(exc.code, exc.message, exc.status_code).

    Debug:
        409 vs 422: conflicts are ServiceError; format errors may be ValueError.
    """
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
    """Any FastAPI HTTPException becomes the doc's error envelope.

    Flow:
        Map status via _STATUS_CODES (else INTERNAL_ERROR) and use exc.detail as message.

    Debug:
        Unknown status (423, 500) becomes INTERNAL_ERROR even if detail is specific.
    """
    _ = request
    return _envelope(
        _STATUS_CODES.get(exc.status_code, "INTERNAL_ERROR"),
        exc.detail,
        exc.status_code,
    )
