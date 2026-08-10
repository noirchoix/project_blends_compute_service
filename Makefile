.PHONY: install test api worker seed audit clean

install:
	python -m pip install -e ".[artifacts,dev]"

test:
	pytest -q

api:
	project-blends-api

worker:
	project-blends-worker

seed:
	PYTHONPATH=src python scripts/seed_project_blends.py --replace

audit:
	PYTHONPATH=src python scripts/audit_sources.py

clean:
	rm -rf .pytest_cache .ruff_cache build dist *.egg-info src/*.egg-info
