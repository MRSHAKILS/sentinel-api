"""FastAPI application: routes, error handling, and hardening.

Status policy:
- 200 success
- 400 invalid JSON / missing or wrong-typed required field
- 422 schema valid but semantically invalid (empty complaint)
- 413 body too large
- 500 internal error (generic body; never leaks input/secrets/traces)

The process must never crash on bad input.
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# Load a local .env if present. override=False so a hosting platform's real env
# vars always win, and so values pre-set by the test harness are respected.
load_dotenv(override=False)

from . import __version__  # noqa: E402
from .pipeline import analyze  # noqa: E402
from .schemas import AnalyzeRequest  # noqa: E402

logger = logging.getLogger("queuestorm")

MAX_BODY_BYTES = int(os.getenv("MAX_BODY_BYTES", "262144"))  # 256 KB default

app = FastAPI(
    title="QueueStorm Investigator",
    version=__version__,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


class EmptyComplaintError(Exception):
    """Raised when `complaint` is present but blank -> HTTP 422."""


# ---------------------------------------------------------------------------
# Hardening middleware: cap request body size (DoS guard).
# ---------------------------------------------------------------------------


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            if int(cl) > MAX_BODY_BYTES:
                return JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={"error": "request body too large"},
                )
        except ValueError:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "invalid content-length header"},
            )
    return await call_next(request)


# ---------------------------------------------------------------------------
# Exception handlers.
# ---------------------------------------------------------------------------


@app.exception_handler(RequestValidationError)
async def on_validation_error(request: Request, exc: RequestValidationError):
    # Invalid JSON, missing required field, or wrong-typed required field.
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": "invalid or malformed request body"},
    )


@app.exception_handler(EmptyComplaintError)
async def on_empty_complaint(request: Request, exc: EmptyComplaintError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"error": "complaint must not be empty"},
    )


@app.exception_handler(Exception)
async def on_unhandled(request: Request, exc: Exception):
    # Generic body only — no stack trace, no input echo, no secrets.
    logger.exception("unhandled error processing request")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "internal error"},
    )


# ---------------------------------------------------------------------------
# Routes.
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/analyze-ticket")
def analyze_ticket(req: AnalyzeRequest) -> JSONResponse:
    # Defined as a SYNC handler on purpose: the pipeline makes a blocking
    # (optional) LLM HTTP call, so FastAPI runs this in its threadpool instead of
    # on the event loop. That keeps /health and concurrent requests responsive.
    if not req.complaint.strip():
        raise EmptyComplaintError()
    result = analyze(req)
    # `result` is a plain dict already validated against the config enums by the
    # pipeline; return it directly for speed and full control over field order.
    return JSONResponse(status_code=status.HTTP_200_OK, content=result)
