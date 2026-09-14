"""Opt-in DeerFlow extension. Normal chat and global tool configuration are untouched."""

from deerflow_extension_api import extension

from .api import build_router
from .config import ROOT, load_settings
from .service import ResearchService


@extension(api="0.2.0", name="deepresearch")
def install(registry, config):
    path = config.get("config_path", "deepresearch.example.yaml")
    settings = load_settings(ROOT / path)
    service = ResearchService(settings)
    registry.service(service)
    registry.routers([build_router(service)])
