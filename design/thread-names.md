# Design Note: Replace Thread IDs with Unique Names

## Motivation

Loom previously used generated two-letter IDs like `AA` and stored a separate thread `name`. Replacing generated IDs with unique names makes thread identity match how humans already think about threads.

## Proposed direction

Make the thread name the canonical identity:

- thread names must be unique
- thread directories should be keyed by that unique name
- CLI arguments that currently take a thread ID should take the unique name instead

## File layout implications

Replace:

- `.loom/threads/<ID>/_thread.md`
- `.loom/threads/<ID>/<task>.md`

With:

- `.loom/threads/<thread-name>/_thread.md`
- `.loom/threads/<thread-name>/<task>.md`

## Command UX

- `loom agent new-task --thread release-planning`
- `loom agent next --thread release-planning`
- duplicate names should fail, not warn

## Migration path

1. teach Loom to resolve both ID-backed and name-backed threads
2. enforce uniqueness for new thread names
3. migrate on-disk directories and rewrite stored references
4. remove generation and use of two-letter thread IDs

## Risks

- reference breakage for task IDs and `depends_on`
- output churn across CLI tests and docs
- name normalization and path safety rules
