from pathlib import Path

from fastapi.testclient import TestClient

from project_blends_compute.api.app import create_app
from project_blends_compute.settings import Settings


def test_health_and_ready_contract(tmp_path: Path):
    settings = Settings(project_root=tmp_path, state_root=tmp_path / "state", artifact_root=tmp_path / "artifacts")
    client = TestClient(create_app(settings))
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["ok"] is True
    ready = client.get("/ready")
    assert ready.status_code == 200
    payload = ready.json()
    assert "lanes" in payload
    assert any(lane["lane"] == "reaction_curation" for lane in payload["lanes"])
