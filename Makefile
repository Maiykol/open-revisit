.PHONY: sync check lint typecheck test test-network dbt app benchmark-app

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
	uv run pytest -m network --no-cov

dbt:
	uv run python -m open_revisit.dbt_runner --config config/dev.yaml

app:
	uv run streamlit run app/streamlit_app.py

benchmark-app:
	uv run python scripts/benchmark_app.py
