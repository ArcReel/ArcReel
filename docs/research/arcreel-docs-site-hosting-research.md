# docs 子域托管方案、国内可达性与搜索方案调研

> 用途：为 `website/`（Docusaurus 文档站）上线 docs.arc-reel.com 的托管与搜索选型提供依据。
> 对应议题：https://github.com/ArcReel/ArcReel/issues/1831（地图 #1829，下游实施票 #1835）。
> 前提：域名 arc-reel.com DNS 托管在 Cloudflare、未 ICP 备案，apex 已由另一个 Cloudflare Pages 项目承载 landing。
> 调研日期：2026-08-13。Cloudflare / Docusaurus 官方结论均经 context7 查询官方文档；网络现状类信息采自社区/中文实践资料并标注来源；本机无法从大陆节点发起拨测，未能实测的项按「需人工核实」标注。

---

## 一、monorepo 子目录 `website/` 建第二个 Cloudflare Pages 项目

### 方案 A：Git 集成（官方 monorepo 支持）

Cloudflare Pages 官方支持同一 Git 仓库连接多个 Pages 项目，各项目独立设置 build command 与 **root directory**（告诉 Pages 在哪个子目录执行构建）；项目名须全局唯一（来源：https://developers.cloudflare.com/pages/configuration/monorepos/ ）。

- **路径过滤（build watch paths）**：默认仓库任何文件变更都会触发所有关联项目的构建；用 include/exclude 路径规则可让与 `website/` 无关的提交跳过构建（来源：https://developers.cloudflare.com/pages/configuration/build-watch-paths/ ，monorepos 文档中以双项目为例专门说明此用法）。
- **限制**：monorepo 支持要求 Build System V2 及以上；同一仓库最多 5 个 Pages 项目（可申请提额）（来源：monorepos 文档 Limitations 节）。
- **Git 集成附带能力**：任意分支的 preview deployment、PR 内的 preview URL 评论、构建/部署状态 check、commit message 跳过构建（来源：https://developers.cloudflare.com/pages/configuration/git-integration/ ）。
- **约束**：构建在 Cloudflare 构建环境执行，受账户级每月构建次数配额限制（免费计划配额见官方 Pages 定价页，本文未单独核实具体数字）；构建环境的 Node/pnpm 版本由环境变量控制，Docusaurus 属标准 Node 构建，无特殊要求。

### 方案 B：GitHub Actions + wrangler（Direct Upload）

在 Actions 里自行构建，然后用 `cloudflare/wrangler-action@v3` 执行 `pages deploy <输出目录> --project-name=<项目名>` 直传产物（来源：https://developers.cloudflare.com/pages/how-to/use-direct-upload-with-continuous-integration/ ）：

- 需要 secrets：`CLOUDFLARE_API_TOKEN`（权限 Account → Cloudflare Pages → Edit）与 `CLOUDFLARE_ACCOUNT_ID`；传 `gitHubToken` 可回写 GitHub deployment status。
- `wrangler pages deploy` 支持 `--branch` 区分 production / preview 部署（来源：https://developers.cloudflare.com/pages/get-started/direct-upload/ 及 wrangler v4 CLI 定义；v4 中 `pages publish` 已更名 `pages deploy`）。
- **约束**：路径过滤要自己写（`on.push.paths: ["website/**"]`）；Node 环境、依赖缓存自己维护；PR preview URL 评论等 Git 集成体验需借 wrangler-action 输出自行拼装；Direct Upload 项目与 Git 集成项目是两种项目形态，不能对同一项目混用两种部署方式。

### 附注：Workers Static Assets

Cloudflare 官方最佳实践现已把 **Workers Static Assets** 列为新项目部署静态站的推荐方式，Pages 继续可用但新特性投入集中在 Workers（来源：https://developers.cloudflare.com/workers/best-practices/ ）。纯静态站只需配置 `assets.directory` 指向构建输出。

### 结论（Q1）

**推荐方案 A（Git 集成）**：dashboard 新建第二个 Pages 项目连同一仓库，root directory 设 `website/`，build command 设 Docusaurus 构建，build watch paths include 设 `website/*`。理由：与 apex landing 项目形态一致（同一账户、同一 zone、同一运维心智）、零 CI 代码维护、免费获得 PR preview。方案 B 仅在构建需求超出 Cloudflare 构建环境（如需要仓库全量工具链）时启用。Workers Static Assets 是长期迁移方向，但当前为与 apex 项目保持一致、且文档站无动态需求，留在 Pages 合理；将来若 landing 迁 Workers 可一并迁移。

---

## 二、docs.arc-reel.com 在中国大陆的可达性

### 事实底座（官方）

- Cloudflare **China Network**（大陆节点加速）仅限 Enterprise 计划的附加订阅，且要求每个接入的 apex 域名有有效 ICP 备案（来源：https://developers.cloudflare.com/china-network/ ）。本项目无备案、非 Enterprise，**不适用**——即站点只能从 Cloudflare 全球网络（境外节点）服务大陆访客，这决定了「可达但有延迟」的天花板。
- Pages 自定义子域：域名 zone 已在同账户 Cloudflare 托管时，在 Pages 项目 Custom domains 里添加 `docs.arc-reel.com` 后 CNAME 记录自动创建；不能只手动加 CNAME 而不在 dashboard 关联，否则 522（来源：https://developers.cloudflare.com/pages/configuration/custom-domains/ ）。自定义域名在免费计划可用。

### `*.pages.dev` 被墙 vs 自定义域名

- `*.pages.dev` / `*.workers.dev` 后缀在大陆长期被 DNS 污染/阻断，社区与中文实践文章均有记载（来源：https://community.cloudflare.com/t/cloudflare-pages-is-inaccessible-in-china/777481 、https://cloud.tencent.com/developer/article/2133923 ）。
- 阻断针对的是 `pages.dev` 这一域名（SNI/DNS 层面），**不是** Cloudflare Pages 服务本身：多篇中文实践文章证实给 Pages 项目绑定自定义域名后大陆可正常打开（来源：https://www.cfmem.com/2021/08/cloudflare-pages.html 、https://blog.csdn.net/xxxlxxxxlx/article/details/155951727 ）。原理：自定义域名走自己的 SNI 与 Cloudflare anycast IP，不命中对 `pages.dev` 的封锁规则。
- 体验上限：无备案走境外节点（大陆访客常落香港/美西 PoP），延迟明显高于国内 CDN，个别 ISP/时段有波动；社区亦有个别 Cloudflare IP 段被部分省份运营商阻断的报告（来源：https://community.cloudflare.com/t/my-website-uses-cloudflare-cdn-but-access-from-mainland-china-is-restricted/786730 ）。若将来需要实质加速，社区通行做法是国内侧套腾讯云 CDN / EdgeOne 回源（需备案），见 https://makerjackie.com/blog/2026-04-25-cloudflare-tencent-cdn-edgeone-pages ——当前阶段不建议引入。

### 先例：arc-reel.com apex 现状

- 本机核实：`https://arc-reel.com` 正常返回（`server: cloudflare`，301 → `/zh/`），确认 apex 已由 Cloudflare 全球网络承载。
- **大陆拨测未能完成**：本机 DNS 处于 fake-IP 代理环境（解析结果 198.18.0.0/15 段），且 itdog.cn 等多节点拨测工具为交互式页面，无法脚本化发起。**需人工核实**：浏览器打开 https://www.itdog.cn/http/ 或 https://www.17ce.com/ ，分别测 `https://arc-reel.com` 与上线后的 `https://docs.arc-reel.com`，关注大陆各省电信/联通/移动的 HTTP 可用率与耗时。

### 结论（Q2）

docs.arc-reel.com 作为同 zone 子域绑定自定义域名后，DNS 与 TLS 路径和 apex 完全一致，**可达性与 apex 同命**——apex 今天大陆可达，docs 子域即同等可达；不要向大陆用户分发任何 `*.pages.dev` 链接。预期是「可达、慢」而非「快」，这是无备案前提下的天花板。上线后用 itdog/17ce 各测一轮留档；若 apex 拨测本身不佳，则是 zone 级问题，与本次选型无关。

---

## 三、Docusaurus 搜索方案

Docusaurus 官方列出的选项：Algolia DocSearch（官方一等支持）、Typesense DocSearch、本地搜索、自定义 SearchBar（来源：https://docusaurus.io/docs/search ）。

### Algolia DocSearch

- **申请**：免费计划面向「developer documentation / technical blog」，经 Algolia dashboard 提交域名，自动审核 + 必要时 1–2 个工作日人工审核，批准后 7 天内完成域名所有权验证（来源：https://docsearch.algolia.com/docs/who-can-apply/ ）。ArcReel 文档站属技术文档，资格上大概率可过，但站点须已上线且"production-ready"——即**须先上线后申请**，存在时间差。
- **集成**：`@docusaurus/preset-classic` 内置 Algolia 主题，批准后在 `themeConfig.algolia` 填 `appId`/`apiKey`/`indexName` 即可（来源：https://docusaurus.io/docs/search#using-algolia-docsearch ）。
- **国内连通性**：algolia.net 未见被墙报告，但 Algolia 无大陆节点、最近为香港，中文社区实测搜索请求延迟约 600–700ms，体验明显偏慢（来源：https://blog.csdn.net/weixin_42429718/article/details/125566181 等中文实践文章；「未被墙」为社区经验，非官方承诺）。搜索是浏览器直连 Algolia 的前端请求，站点本身可达不能改善它。

### 本地搜索：@easyops-cn/docusaurus-search-local

- 构建期生成索引、纯浏览器端检索、零外部请求——国内可达性与站点本身完全一致，无第三方依赖。
- 明确支持 Docusaurus v2/v3，**对中文分词专门优化**（`language: ["en", "zh"]`，支持 `zhUserDict` 自定义词典），维护活跃（来源：https://github.com/easyops-cn/docusaurus-search-local ）。
- 代价：索引打进站点产物、体积随文档量增长；无 Algolia 的排序/analytics。文档站初期规模下不构成问题。

### Typesense / 自建

需自托管搜索服务，运维成本与本项目「静态站 + 免运维」定位不符，不展开。

### 结论（Q3）

**推荐 @easyops-cn/docusaurus-search-local**：与「大陆可达」目标自洽（无任何境外 API 依赖）、中文分词开箱即用、无申请流程阻塞上线。Algolia DocSearch 作为后备演进项：待站点上线稳定、文档量大到本地索引体积成负担时再申请，届时需接受大陆搜索延迟或自跑 crawler。

---

## 总结论

| 问题 | 结论 |
|---|---|
| 第二个 Pages 项目机制 | Git 集成：root directory=`website/` + build watch paths include `website/*`；Actions+wrangler 仅作构建环境不满足时的后备 |
| docs 子域大陆可达性 | 同 zone 自定义域名与 apex 同命，可达但走境外节点偏慢；禁用 `*.pages.dev` 链接对大陆分发；上线后人工 itdog/17ce 拨测留档 |
| 搜索 | @easyops-cn/docusaurus-search-local（中文分词、零外部依赖）；Algolia DocSearch 留作规模化后备 |
