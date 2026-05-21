# Codex Project Instructions

## Git Branch Sync Rule

For Codex-made project changes in this repository:

- Work from `dev-mac` for macOS/Codex App development.
- Commit meaningful Codex changes locally on `dev-mac`.
- Also sync the same commit locally to `dev-linux` before finishing the task.
- Keep `dev-mac` and `dev-linux` at the same commit unless the user explicitly asks for different branch contents.
- Do not include unrelated local files such as `.DS_Store` in commits.
- If the branches diverge or a fast-forward sync is not possible, stop and ask the user before resolving the divergence.
- Push branches only when the user asks for a push or remote sync.

Preferred local sync flow after committing on `dev-mac`:

```bash
git checkout dev-linux
git merge --ff-only dev-mac
git checkout dev-mac
```
