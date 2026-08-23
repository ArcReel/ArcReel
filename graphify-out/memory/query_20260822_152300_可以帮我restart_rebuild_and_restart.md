---
type: "query"
date: "2026-08-22T15:23:00.810653+00:00"
question: "可以帮我restart，rebuild and restart。"
contributor: "graphify"
outcome: "useful"
source_nodes: ["health_check", "server", "frontend/package.json"]
---

# Q: 可以帮我restart，rebuild and restart。

## Answer

Expanded from graph vocab: [dev, server, frontend, backend, docker, compose, build, restart, health, uvicorn]. 当前 ArcReel 由 scripts/dev.sh 本地开发栈运行，不是 Docker：Uvicorn 127.0.0.1:1241 + Vite localhost:5173。执行 pnpm build 成功（TypeScript + Vite production build）；旧进程组 42800 的 Vite 响应 SIGINT 退出，Uvicorn/reloader 残留经精确 PID TERM/KILL 回收，两个端口释放；重新运行 scripts/dev.sh，Alembic upgrade head 成功、数据库 schema 最新、Uvicorn startup complete、Vite ready。健康验证：GET /health 返回 status ok，前端 HTTP 200，新 PID 46068/46070 监听 1241，PID 46084 监听 5173。匿名 /api/v1/providers 返回 401，符合鉴权预期。

## Outcome

- Signal: useful

## Source Nodes

- health_check
- server
- frontend/package.json