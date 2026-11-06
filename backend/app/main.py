"""DocuMind API entrypoint: ``uvicorn app.main:app``."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.api.router import api_router
from app.core.body_limit import BodySizeLimitMiddleware
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.core.resources import AppResources, build_resources

_JSON_BODY_LIMIT = 2 * 1024 * 1024
_MULTIPART_OVERHEAD = 1024 * 1024


def create_app(settings: Settings | None = None, resources: AppResources | None = None) -> FastAPI:
    """Build the app. Tests pass pre-built ``resources``; otherwise they are created in the lifespan."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = None
        if resources is None:
            owned = await build_resources(settings)
            app.state.resources = owned
        try:
            yield
        finally:
            if owned is not None:
                await owned.aclose()

    app = FastAPI(
        title=f"{settings.app_name} API",
        version="0.1.0",
        description="Enterprise Document Intelligence Platform — documents, RAG chat with citations, evaluation.",
        lifespan=lifespan,
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
        docs_url="/docs",
        redoc_url=None,
    )
    if resources is not None:
        app.state.resources = resources

    register_exception_handlers(app)

    # Order: the last added middleware runs first.
    app.add_middleware(
        BodySizeLimitMiddleware,
        default_limit=_JSON_BODY_LIMIT,
        upload_limit=settings.max_upload_size_bytes * settings.max_files_per_upload + _MULTIPART_OVERHEAD,
        upload_path_suffix="/documents/upload",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Retry-After", "Content-Disposition"],
    )
    app.add_middleware(RequestContextMiddleware, hsts=settings.is_production)

    app.include_router(health.router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


def _default_app() -> FastAPI:
    return create_app()


# Lazily constructed so importing app.main (e.g. in tests) doesn't require a full environment.
def __getattr__(name: str) -> FastAPI:
    if name == "app":
        application = _default_app()
        globals()["app"] = application
        return application
    raise AttributeError(name)
