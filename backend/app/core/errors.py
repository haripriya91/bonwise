from sqlalchemy.exc import TimeoutError as PoolTimeout

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


async def safe_validation_error(
    request: Request,
    exc: RequestValidationError,
):
    return JSONResponse(
        status_code=422,
        content={
            "detail": (
                "Invalid request. Check required fields, "
                "types and size limits."
            )
        },
        headers={"Cache-Control": "no-store"},
    )


async def database_unavailable(request, exc):
    # Never return database host, SQL, credentials or driver exception messages.
    transient = isinstance(exc, PoolTimeout) or type(getattr(exc, "orig", None)).__name__ in {"ConnectionTimeout", "OperationalError"}
    return JSONResponse(status_code=503 if transient else 500,
        content={"detail": "Database temporarily unavailable. Please retry shortly." if transient else "Database request failed."},
        headers={"Cache-Control": "no-store", **({"Retry-After": "5"} if transient else {})})
