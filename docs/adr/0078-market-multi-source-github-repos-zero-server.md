---
status: accepted
---

# 市场源索引直接承载浏览与安装

市场源是 GitHub 仓库或等价的 HTTPS 索引地址。实例通过本地后端获取索引和条目，浏览及安装无需中心服务或账号。官方源随版提供，可禁用，且可作为第三方源的模板。第三方源由用户自行添加。后台经索引下载并校验条目，调用端点在本地事务中安装，安装记录见 [ADR 0079](0079-market-installation-separate-entity-editable.md)。

把索引与安装放在各个市场源，让自部署实例和第三方源都能独立运转。安装统计、评分与分享提交由可选的官方服务提供，分开见 [ADR 0093](0093-instance-id-and-install-report.md) 和 [ADR 0094](0094-submission-via-official-service-to-market-source-pr.md)。官方源的发布审批保留在 GitHub PR，用户可手工投稿。源仓库的索引与校验入口见 [市场工具包](../../packages/arcreel-market-core/README.md)。

- **raw 文件地址**。GitHub 形态的源读取 raw 文件，统一使用可配置代理前缀并避开 GitHub API 匿名限流。索引提供浏览所需的元数据，下载才读取条目定义。后端抓取的地址、解析结果、重定向与响应大小受[抓取边界](../../packages/arcreel-market-core/src/arcreel_market_core/market/fetch.py)限制。
- **CI 生成官方索引**。索引是条目元信息的投影，贡献者只改条目目录。安装时拒绝索引与定义不一致的条目。用同一工具校验定义与源目录，源仓复用应用仓的校验工作流。索引格式允许旧客户端忽略未知可选字段，而条目需要新版时用最低应用版本表达。
