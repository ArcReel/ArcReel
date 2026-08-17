# 供应商 API 文档索引维护

本目录只保存导航：官方来源链接、ArcReel 依赖的主题和稳定代码入口。运行时事实仍归 registry、backend 与 endpoint 声明，不从索引反向加载。

## 修改流程

1. 以 `PROVIDER_REGISTRY` / `ENDPOINT_REGISTRY` 的规范 id 定位对应文件；一个 id 一个文件。
2. 用官方公开页面核对接口、能力与参数约束、计费。普通抓取不可用时依次尝试 Jina Reader、`agent-browser` 或用户浏览器，来源仍保持官方。
3. 更新本 id 的官方入口、ArcReel 实际依赖页面和代码 `path::symbol`。共享文档可重复链接，不复制页面内容或数值。
4. 新增或删除 registry 条目时同步更新本目录对应 README；完成标准是每个规范 id 都能从 README 到达一份同名索引，且索引能到达官方来源和实际消费代码。

保存测试所需的最小请求/响应 fixture 属于代码契约，不属于文档镜像；保持 fixture 最小并附来源。仓库不保存第三方文档正文、完整参数/价格表或用于重建镜像的下载脚本。
