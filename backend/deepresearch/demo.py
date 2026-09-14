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
    if os.getenv("DEEPRESEARCH_DEMO_DATA_DIR"):
        settings = settings.model_copy(update={"data_dir": os.environ["DEEPRESEARCH_DEMO_DATA_DIR"]})
    if settings.runner != "demo":
        raise RuntimeError("Standalone demo refuses real MCP mode; use authenticated DeerFlow extension")
    service = ResearchService(settings)
    frontend_port = int(os.getenv("DEEPRESEARCH_DEMO_FRONTEND_PORT", "3000"))
    if not 1 <= frontend_port <= 65535:
        raise ValueError("Invalid demo frontend port")
    origins = [f"http://localhost:{frontend_port}", f"http://127.0.0.1:{frontend_port}"]

    @asynccontextmanager
    async def lifespan(app):
        await service.start()
        try:
            yield
        finally:
            await service.stop()

    app = FastAPI(title="DeepResearch local synthetic demo", lifespan=lifespan)
    app.state.research = service
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["Content-Type", "Idempotency-Key", "Last-Event-ID"], allow_credentials=True)
    app.include_router(build_router(service, local_demo=True, demo_origins=origins))
    return app


app = create_app()
