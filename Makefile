.PHONY: install install-dev run tunnel test lint fmt clean

install:
	uv sync --no-dev

install-dev:
	uv sync --all-extras

CONFIG ?= config.yaml

run: install
	uv run github-webhook --config $(CONFIG)

tunnel:
	cloudflared tunnel --url http://localhost:5000

test: install-dev
	uv run pytest -v

lint: install-dev
	uv run ruff check src/ tests/
	uv run mypy src/

fmt: install-dev
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

clean:
	rm -rf .venv dist build *.egg-info src/*.egg-info .mypy_cache .pytest_cache .ruff_cache
