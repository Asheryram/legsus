.PHONY: dev test lint up down logs

dev:            ## run locally with auto-reload
	cd backend && uvicorn app.main:app --reload --port 8000

test:
	cd backend && python -m pytest -q

lint:
	cd backend && ruff check app tests

up:             ## build + run in Docker
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f
