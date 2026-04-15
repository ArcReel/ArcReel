dev:
	@trap 'kill 0' INT TERM EXIT; \
	LOG_LEVEL=INFO uv run uvicorn server.app:app --reload --port 1241 & \
	cd frontend && pnpm dev & \
	wait

dev-backend:
	LOG_LEVEL=INFO uv run uvicorn server.app:app --reload --port 1241

dev-frontend:
	cd frontend && pnpm dev
