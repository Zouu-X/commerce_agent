.PHONY: up down logs smoke test lint frontend-install seed reindex reset-demo migrate eval eval-mock eval-retrieval python-lock

DOCKER ?= docker
API_URL ?= http://localhost:8000
WEB_URL ?= http://localhost:5173
RETRIEVAL_SPLIT ?= all
PYTHON_LOCK_IMAGE ?= commerce-agent-python-lock

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
	$(DOCKER) compose exec api python -m app.knowledge.reindex

reindex:
	$(DOCKER) compose exec api python -m app.knowledge.reindex --force

reset-demo: seed

eval:
	$(DOCKER) compose build api
	$(DOCKER) compose run --rm api sh -c "alembic upgrade head && python -m app.commerce.seed && python -m app.knowledge.reindex && python -m app.evaluations.cli --output-dir /app/eval-results"

eval-mock:
	$(DOCKER) compose build api
	MODEL_PROVIDER=mock MODEL_NAME=mock-commerce-agent MODEL_INPUT_COST_PER_MILLION=0 MODEL_OUTPUT_COST_PER_MILLION=0 $(DOCKER) compose run --rm api sh -c "alembic upgrade head && python -m app.commerce.seed && python -m app.knowledge.reindex && python -m app.evaluations.cli --output-dir /app/eval-results"

eval-retrieval:
	$(DOCKER) compose build api
	$(DOCKER) compose run --rm api sh -c "alembic upgrade head && python -m app.commerce.seed && python -m app.knowledge.reindex && python -m app.evaluations.retrieval_cli --split $(RETRIEVAL_SPLIT) --output-dir /app/eval-results"

frontend-install:
	npm --prefix frontend install

test:
	$(DOCKER) build --target test backend
	npm --prefix frontend run test
	npm --prefix frontend run build

lint:
	$(DOCKER) build --target test backend
	npm --prefix frontend run lint

# pyproject.toml owns the allowed dependency ranges. This target resolves the
# dev + embedding union into exact CPython 3.12/Linux pins consumed by CI and Docker.
python-lock:
	$(DOCKER) build --target lock --tag $(PYTHON_LOCK_IMAGE) backend
	$(DOCKER) run --rm \
		--mount type=bind,source="$(CURDIR)/backend",target=/workspace \
		$(PYTHON_LOCK_IMAGE) \
		--upgrade \
		--resolver=backtracking \
		--strip-extras \
		--extra=dev \
		--extra=embedding \
		--output-file=requirements-py312-linux.lock \
		pyproject.toml
