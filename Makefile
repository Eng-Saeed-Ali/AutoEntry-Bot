# =============================================================================
# AutoEntry Bot — Makefile
# =============================================================================
# Usage:
#   make dev          — Install deps in dev mode (venv + editable install)
#   make lint         — Run ruff format check + lint + mypy
#   make format       — Auto-format code with ruff
#   make test         — Run full test suite
#   make test-unit    — Run only unit tests (skip integration)
#   make migrate-up   — Apply pending Alembic migrations
#   make migrate-down — Rollback last migration
#   make build        — Build Docker image
#   make up           — Start Docker Compose services
#   make down         — Stop Docker Compose services
#   make clean        — Remove build artifacts, caches, __pycache__
# =============================================================================

.PHONY: help dev lint format test test-unit test-cov migrate-up migrate-down build up down clean

# --- Default target ---
help:
	@echo "Available targets:"
	@echo "  dev          — Install deps + editable install"
	@echo "  lint         — ruff check + mypy"
	@echo "  format       — ruff format"
	@echo "  test         — Full test suite"
	@echo "  test-unit    — Unit tests only (no integration)"
	@echo "  test-cov     — Tests with HTML coverage report"
	@echo "  migrate-up   — Apply DB migrations"
	@echo "  migrate-down — Rollback last migration"
	@echo "  build        — Build Docker image"
	@echo "  up           — docker compose up -d"
	@echo "  down         — docker compose down"
	@echo "  clean        — Remove cache/build artifacts"

# --- Development Setup ---
dev:
	python -m venv .venv
	.venv\Scripts\pip install --upgrade pip 2>nul || .venv/bin/pip install --upgrade pip
	.venv\Scripts\pip install -e ".[dev]" 2>nul || .venv/bin/pip install -e ".[dev]"
	@echo "✅ Dev environment ready. Activate: .venv\Scripts\activate (Windows) or source .venv/bin/activate (Unix)"

# --- Linting & Type Checking ---
lint:
	ruff check src/ tests/
	mypy src/domain/ --strict
	mypy src/

format:
	ruff format src/ tests/
	ruff check --fix src/ tests/

# --- Testing ---
test:
	pytest -v -m "not integration"

test-unit:
	pytest -v tests/domain/ tests/application/ tests/infrastructure/

test-cov:
	pytest -v --cov=src --cov-report=html --cov-report=term-missing
	@echo "📊 HTML report: htmlcov/index.html"

# --- Database Migrations ---
migrate-up:
	alembic upgrade head

migrate-down:
	alembic downgrade -1

# --- Docker ---
build:
	docker build -t autoentry-bot:latest .

up:
	docker compose up -d
	@echo "🐳 Services running. Check: docker compose ps"

down:
	docker compose down

# --- Cleanup ---
clean:
	@echo "🧹 Cleaning build artifacts..."
	@rmdir /s /q __pycache__ 2>nul || rm -rf __pycache__
	@for /d /r . %d in (__pycache__) do @if exist "%d" rmdir /s /q "%d" 2>nul || find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
	@rmdir /s /q .mypy_cache 2>nul || rm -rf .mypy_cache
	@rmdir /s /q .pytest_cache 2>nul || rm -rf .pytest_cache
	@rmdir /s /q htmlcov 2>nul || rm -rf htmlcov
	@del /q .coverage .coverage.* 2>nul || rm -f .coverage .coverage.*
	@rmdir /s /q *.egg-info 2>nul || rm -rf *.egg-info
	@echo "✅ Clean complete."