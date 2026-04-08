# Contributing Guide

Contributions, bug reports, and feature suggestions are welcome!

## Local Development Environment

```bash
# Prerequisites: Python 3.12+, Node.js 20+, uv, pnpm, ffmpeg

# Install dependencies
uv sync
cd frontend && pnpm install && cd ..

# Initialize database
uv run alembic upgrade head

# Start backend (terminal 1)
uv run uvicorn server.app:app --reload --port 1241

# Start frontend (terminal 2)
cd frontend && pnpm dev

# Visit http://localhost:5173
```

## Running Tests

```bash
# Backend tests
python -m pytest

# Frontend typecheck + tests
cd frontend && pnpm check
```

## Code Quality

**Lint & Format (ruff):**

```bash
uv run ruff check . && uv run ruff format .
```

- Rule sets: `E`/`F`/`I`/`UP`, ignoring `E402` and `E501`
- line-length: 120
- Enforced in CI: `ruff check . && ruff format --check .`

**Test Coverage:**

- CI requires ≥80%
- `asyncio_mode = "auto"` (no need to manually mark async tests)

## Commit Convention

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/) format:

```
feat: description of new feature
fix: description of bug fix
refactor: description of refactoring
docs: documentation changes
chore: build/tooling changes
```
