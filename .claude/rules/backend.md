---
paths:
  - "lib/**"
  - "server/**"
  - "packages/*/src/**"
---

# Backend

Before writing cancellation-sensitive asynchronous code (timeout blocks, calling `cancel()` on running tasks, writing formal files in threads, or recording results after a provider returns), read “8.3 取消与中断的来源” in `website/docs/dev/architecture.md`.
