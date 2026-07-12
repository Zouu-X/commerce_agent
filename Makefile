.PHONY: up down logs test lint frontend-install seed reset-demo migrate

DOCKER ?= docker

up:
	$(DOCKER) compose up --build

down:
	$(DOCKER) compose down

logs:
	$(DOCKER) compose logs -f

migrate:
	$(DOCKER) compose exec api alembic upgrade head

seed:
	$(DOCKER) compose exec api python -m app.commerce.seed

reset-demo: seed

frontend-install:
	npm --prefix frontend install

test:
	$(DOCKER) build --target test backend
	npm --prefix frontend run build

lint:
	$(DOCKER) build --target test backend
	npm --prefix frontend run lint
