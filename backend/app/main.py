from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.account import router as account_router
from app.api.purchases import router as purchases_router

from fastapi.exceptions import RequestValidationError

from app.api.extraction import router as extraction_router
from app.core.errors import safe_validation_error

from app.api.image_capture import router as image_capture_router

from app.core.config import get_settings


settings = get_settings()

app = FastAPI(
    title="Bonwise API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_methods=[
        "GET",
        "POST",
        "PATCH",
        "DELETE",
        "OPTIONS",
    ],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Idempotency-Key",
    ],
)
app.include_router(account_router)
app.include_router(purchases_router)
app.include_router(image_capture_router)

app.add_exception_handler(
    RequestValidationError,
    safe_validation_error,
)

app.include_router(extraction_router)

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "bonwise-api",
    }