from __future__ import annotations

import uvicorn

from .app import app


def run() -> None:
    uvicorn.run("project_blends_compute.api.app:app", host="0.0.0.0", port=8000, reload=False)


__all__ = ["app", "run"]


if __name__ == "__main__":
    run()
