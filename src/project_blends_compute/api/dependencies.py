from __future__ import annotations

from fastapi import Header, HTTPException, Request

from project_blends_compute.orchestrator import RunManager
from project_blends_compute.settings import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_manager(request: Request) -> RunManager:
    return request.app.state.manager


def require_api_key(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    settings: Settings = request.app.state.settings
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail={"code": "invalid_api_key", "message": "A valid X-API-Key header is required"})
