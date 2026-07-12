.PHONY: up down logs test lint frontend-install

DOCKER ?= docker

up:
	$(DOCKER) compose up --build

down:
	$(DOCKER) compose down

logs:
	$(DOCKER) compose logs -f

frontend-install:
	npm --prefix frontend install

test:
	$(DOCKER) build --target test backend
	npm --prefix frontend run build

lint:
	$(DOCKER) build --target test backend
	npm --prefix frontend run lint
