---
status: accepted
---

# 内嵌 Agent 以短期 JWT 和开放网络调用同源 HTTP API

自定义调用端点适配需要内嵌 Agent 同时读取任意供应商文档，并调用 ArcReel 已有的 validate、端点测试
和保存 HTTP API。原有 sandbox `allowedDomains` 固定清单无法覆盖用户选择的供应商域名；把这些接口再
包装成常驻 MCP / SDK tools 会制造第二套 Agent 表面。

决定把内嵌 Agent sandbox 的网络配置改为 `allowedDomains: ["*"]` 加 `allowLocalBinding: true`；文件
`denyRead` / `denyWrite`、禁止 unsandboxed fallback 与 Windows 命令白名单保持不变。`WebFetch` 加入
允许工具，`WebSearch` 不加入。每次会话 options 构建时，若认证开启，签发 15 分钟会话 JWT 到
`ARCREEL_API_TOKEN`，并注入 `ARCREEL_API_BASE`（默认 `http://127.0.0.1:1241/api/v1`）；认证关闭时
token 为空。专用 token 名称保留给 sandbox 内 skill 脚本读取，其他 provider / secret-like 环境变量
继续从 Bash 子进程剥离。

两个网络开关都必须显式写出，缺一不可。`allowedDomains` 是预放行清单而非限制清单：省略 `network`
键等于零预放行，无人值守会话里新域名的放行请求被直接拒，出站反而全断。`allowLocalBinding` 单独
控制 loopback，`allowedDomains` 无论写 `*` 还是写 `127.0.0.1` 都覆盖不到它，而 skill 脚本调同源
ArcReel API 必须经 loopback。

此选择在两个方向上扩大信任边界。其一，供应商文档中的 prompt injection 或被接管的 Agent 可在 token
有效期内把它外传到任意域名，也可调用该管理员会话 JWT 能访问的全部 ArcReel API——其中包括明文返回
`api_key` 的自定义供应商凭证端点，因此 Bash 子进程的 secret-like 环境变量剥离对拿到 token 的 Agent
不再构成实际屏障。JWT 无状态，签发后无法提前吊销：唯一的作废手段是轮换 `AUTH_TOKEN_SECRET`，代价
是同时踢掉全部网页会话。15 分钟时效只缩短窗口，不降低权限；它同时是单次会话内 API 调用的可用
窗口——token 不续期，认证开启时会话超过 15 分钟后 skill 脚本的 API 调用将以 401 失败，需重开
会话获取新 token。其二，`allowLocalBinding` 暴露的是整台
宿主的 loopback，不止 ArcReel 自身端口——同机的数据库、其他应用的开发服务器、用户的私有本地服务都
对 sandbox 内 Bash 可达，文件围栏不覆盖这条通路。

接受这两项风险以换取同一 HTTP API 同时服务外部与内嵌 Agent。计费的测试连接与破坏性的覆盖操作仍由
skill 在调用前回问，但这属于 Agent 行为约束，不是安全隔离。
