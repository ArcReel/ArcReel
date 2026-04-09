---
name: release
description: Project release process: ask for the version bump type, update frontend and backend version numbers, lock dependencies, commit, tag, and push. Use this skill when the user mentions "release", "bump version", "tag", or "publish a new version".
---

# Release — Project Release

## Process

### 1. Confirm Version Bump

Read the current version number (line 3 of `pyproject.toml`), then ask the user which bump type they want:

- **patch** (x.y.Z) — bug fixes, minor adjustments
- **minor** (x.Y.0) — new features
- **major** (X.0.0) — breaking changes

Show the current version and the result of each bump type; let the user confirm.

### 2. Update Version Numbers

Modify both files simultaneously (version numbers must always be consistent):

| File | Location | Format |
|------|------|------|
| `pyproject.toml` | Line 3 | `version = "X.Y.Z"` |
| `frontend/package.json` | Line 3 | `"version": "X.Y.Z",` |

### 3. Lock Dependencies

```bash
uv lock
```

This automatically syncs the arcreel package version number in `uv.lock`.

### 4. Commit

Stage the three files (`pyproject.toml`, `frontend/package.json`, `uv.lock`) and commit:

```
chore: bump version to X.Y.Z
```

Note: the version number in the commit message does not have a `v` prefix.

### 5. Tag

```bash
git tag vX.Y.Z
```

The tag name has a `v` prefix.

### 6. Push

Push the commit and tag:

```bash
git push origin main && git push origin vX.Y.Z
```

Confirm with the user before pushing.
