.PHONY: dev test lint format typecheck migrate auth-cli compose-up compose-down

dev: compose-up

test:
	uv run --project apps/api --directory apps/api pytest
	npm test --prefix apps/web

lint:
	uv run --project apps/api --directory apps/api ruff check app tests
	npm run lint --prefix apps/web

format:
	uv run --project apps/api --directory apps/api ruff format app tests
	uv run --project apps/api --directory apps/api ruff check --fix app tests

typecheck:
	uv run --project apps/api --directory apps/api mypy app tests
	npm run typecheck --prefix apps/web

migrate:
	docker compose run --rm migrate

auth-cli:
	docker compose exec -it api python -m app.cli $(ARGS)

compose-up:
	docker compose up --build

compose-down:
	docker compose down
