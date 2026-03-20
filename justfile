# List all available commands
default:
    @just --list

# Install dependencies in development mode
install:
    uv sync --all-groups
    uvx prek install

# Format all code
format:
    just --fmt --unstable
    uvx ruff format .
    uvx ruff check . --fix

# Run static checks
check:
    uvx ruff check .
    uvx ty check src/ tests/

# Run tests
test:
    uv run pytest

# Run tests with coverage
cov:
    uv run pytest --cov=loom --cov-report=term-missing
    uv run coverage xml

# Run pre-commit hooks on all files
pre-commit:
    uvx prek run --all-files

# Preview documentation locally
docs:
    uv run zensical serve --open

# Build documentation site
docs-build:
    uv run zensical build

# Full CI check
ci: pre-commit format check test
