.PHONY: dev test lint format typecheck web-build oidc-check migrate auth-cli compose-up compose-down

dev: compose-up

test:
	cd apps/api && uv run pytest
	npm test --prefix apps/web -- --run

lint:
	cd apps/api && uv run ruff check app tests
	npm run lint --prefix apps/web

format:
	cd apps/api && uv run ruff format app tests && uv run ruff check --fix app tests

typecheck:
	cd apps/api && uv run mypy app tests
	npm run typecheck --prefix apps/web

web-build:
	npm run build --prefix apps/web

oidc-check:
	docker compose run --rm --no-deps api python -m app.cli auth check-oidc

migrate:
	docker compose run --rm migrate

auth-cli:
	docker compose exec -it api python -m app.cli $(ARGS)

compose-up:
	docker compose up --build

compose-down:
	docker compose down
