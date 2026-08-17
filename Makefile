.PHONY: up down logs smoke test lint frontend-install seed reset-demo migrate eval eval-mock

DOCKER ?= docker
API_URL ?= http://localhost:8000
WEB_URL ?= http://localhost:5173

up:
	$(DOCKER) compose up --build --detach --wait

down:
	$(DOCKER) compose down

logs:
	$(DOCKER) compose logs -f

smoke:
	@curl --fail --silent --show-error --output /dev/null $(API_URL)/api/v1/health
	@echo "✓ API health"
	@curl --fail --silent --show-error --output /dev/null $(API_URL)/api/v1/ready
	@echo "✓ API ready"
	@curl --fail --silent --show-error --output /dev/null $(API_URL)/api/v1/demo/contexts
	@echo "✓ Demo data"
	@curl --fail --silent --show-error --output /dev/null $(WEB_URL)/
	@echo "✓ Web"

migrate:
	$(DOCKER) compose exec api alembic upgrade head

seed:
	$(DOCKER) compose exec api python -m app.commerce.seed

reset-demo: seed

eval:
	$(DOCKER) compose build api
	$(DOCKER) compose run --rm api sh -c "alembic upgrade head && python -m app.commerce.seed && python -m app.evaluations.cli --output-dir /app/eval-results"

eval-mock:
	$(DOCKER) compose build api
	MODEL_PROVIDER=mock MODEL_NAME=mock-commerce-agent MODEL_INPUT_COST_PER_MILLION=0 MODEL_OUTPUT_COST_PER_MILLION=0 $(DOCKER) compose run --rm api sh -c "alembic upgrade head && python -m app.commerce.seed && python -m app.evaluations.cli --output-dir /app/eval-results"

frontend-install:
	npm --prefix frontend install

test:
	$(DOCKER) build --target test backend
	npm --prefix frontend run test
	npm --prefix frontend run build

lint:
	$(DOCKER) build --target test backend
	npm --prefix frontend run lint
