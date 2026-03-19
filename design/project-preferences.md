# Upstream Project Preferences and Standards

## Nearby repos checked

- Primary: `/workspace/role-forge`
- Also compared: `/workspace/tensor-spec`, `/workspace/copier-python`
- `volvox` was not available under `/workspace`

## Recommended adoptable standards

1. **Split dev dependencies into `check` / `test` / `docs`.**
2. **Add explicit coverage config to `pyproject.toml`.**
3. **Split tests into `just test-unit` and `just test-e2e`.**
4. **Tighten pytest config with explicit mode choices.**
5. **Split CI into separate static / tests / docs workflows.**
6. **Enforce docs build in CI and expand docs metadata.**
7. **Add a lightweight PR/commit title convention if contributor volume grows.**

## Why these fit Loom

Loom already shares the same broad stack: `uv`, `hatchling`, `src/` layout, `ruff`, `pytest`, `just`, `prek`, and `zensical`. These changes tighten the workflow without changing the repo's basic shape.

## Recommended order

1. dependency-group split
2. coverage config
3. unit/e2e just targets
4. stricter pytest config
5. docs CI
