# Snapshot-style testing adoption

## Findings

- Loom already depends on `syrupy` but does not use snapshots yet.
- Loom's tests are mostly inline assertion-based, especially for CLI flows.
- `/workspace/role-forge` uses snapshots heavily for generated text outputs.

## Recommendation

Adopt snapshot-style tests selectively, not repo-wide.

### Recommended scope

Use snapshots for stable generated text surfaces:

1. `loom init` generated `loom.toml`
2. generated markdown task/thread file content
3. long, mostly static CLI outputs such as `loom agent start`

### What to adopt from role-forge

- `syrupy` snapshots for full generated content
- `tests/__snapshots__/` convention
- keep logic-heavy workflow tests as normal assertions

### What not to adopt

- do not convert the full CLI lifecycle suite to snapshots
- do not snapshot unstable outputs with generated IDs unless normalized first

## Suggested rollout

Phase 1:

- snapshot `loom agent start`
- snapshot default `loom.toml` generation
- snapshot one representative task markdown file
