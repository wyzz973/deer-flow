"""Local synthetic-only API: uvicorn deepresearch.demo:app --host 127.0.0.1 --port 8022."""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import build_router
from .config import load_settings
from .service import ResearchService


def create_app(settings=None):
    settings = settings or load_settings(os.getenv("DEEPRESEARCH_CONFIG"))
    if settings.runner != "demo":
        raise RuntimeError("Standalone demo refuses real MCP mode; use authenticated DeerFlow extension")
    service = ResearchService(settings)

    @asynccontextmanager
    async def lifespan(app):
        await service.start()
        try:
            yield
        finally:
            await service.stop()

    app = FastAPI(title="DeepResearch local synthetic demo", lifespan=lifespan)
    app.state.research = service
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type", "Idempotency-Key", "Last-Event-ID"], allow_credentials=True)
    app.include_router(build_router(service, local_demo=True))
    return app


app = create_app()
