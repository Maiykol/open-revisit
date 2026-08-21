.PHONY: sync check lint typecheck test test-network

sync:
	uv sync --frozen

check: lint typecheck test

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy --strict src/

test:
	uv run pytest -m "not network"

test-network:
	uv run pytest -m network
