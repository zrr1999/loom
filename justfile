# List all available commands
default:
    @just --list

# Install dependencies in development mode
install:
    uv sync --all-groups
    uvx prek install --hook-type pre-commit --hook-type commit-msg

# Format all code
format:
    just --fmt --unstable
    uv run python -m loom.doc_generation
    uvx ruff format .
    uvx ruff check . --fix

# Run static checks
check:
    uv run python -m loom.doc_generation --check
    uvx ruff check .
    uvx ty check src/ tests/

# Run code quality checks
quality-check:
    uvx lizard -i -1 src/

# Sync generated documentation blocks
docs-sync *args='':
    uv run python -m loom.doc_generation {{ args }}

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
ci: pre-commit format check quality-check test
