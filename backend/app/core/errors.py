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