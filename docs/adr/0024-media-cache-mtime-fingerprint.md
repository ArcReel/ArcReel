---
status: accepted
---

# 媒体缓存用文件 mtime 作内容寻址 cache-bust，版本/带参文件设 immutable

session 级 revision 计数器在刷新页面、跨 session 重进、虚拟滚动重挂载时都会按计数器重新下载，且新 session 的 `?v=0` 可能对应已变内容。决定前端用文件 mtime（纳秒）作为资产 URL 的 `?v=` 参数（asset fingerprint）替代计数器，文件路由对带 `?v=` 或路径含 `versions/` 的请求设 `Cache-Control: public, max-age=31536000, immutable`——「内容不变→URL 不变→disk cache 命中」对上述所有场景都成立。

## Consequences

- fingerprint 需随项目 API 与 SSE 事件下发、前端维护 fingerprint store。
- `immutable` 头一旦发出客户端会长期缓存；正因如此 fingerprint 必须随内容变化，且 `versions/` 下不可变历史文件天然适用。
