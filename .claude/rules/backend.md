---
paths:
  - "lib/**"
  - "server/**"
  - "packages/*/src/**"
---

# 后端

写对取消敏感的异步代码（超时块、对执行中任务调用 `cancel()`、线程里写正式文件、供应商返回后的落账）前，先读 `website/docs/dev/architecture.md`「8.3 取消与中断的来源」。
