---
status: accepted
---

# 正式容器镜像以单次多架构构建同步分发到双 registry

正式版本镜像由 `v*` tag 触发发布，同时面向 GHCR（`ghcr.io`）与 Docker Hub（`docker.io`）提供拉取入口，实现见 [docker-release.yml](../../.github/workflows/docker-release.yml)。在两处 registry 各自独立构建会放大构建耗时，并可能因基础镜像层或上游包更新造成同一版本标签指向不同产物；因此正式发布以同一次 `linux/amd64` 与 `linux/arm64` 多架构构建（附带 SBOM 与 provenance）同时推送到两处 registry，确保用户在任一入口拉取同一标签得到相同内容。

推送前先分别构建 `linux/amd64` 与 `linux/arm64` 镜像并执行 Trivy 漏洞扫描，存在可修复的 `CRITICAL` 漏洞时阻断发布，避免只扫描单一架构而放过另一架构变体中的问题；两个架构的构建层写入 GitHub Actions 缓存，供最终多架构构建与双推步骤复用。

发布工作流以 `concurrency` 组 `docker-release`（`cancel-in-progress: false`）串行执行，避免多个 `v*` tag 并发构建与推送时发生竞态、互相覆盖 `latest`、`{{major}}` 等可变标签。队列策略设为 `queue: max` 而非默认的 `queue: single`，使连续推送多个版本 tag 时排队保留中间版本而不被覆盖丢弃，实际容量受平台单个并发组的待运行队列上限约束。该串行策略针对并发推送竞态，按排队顺序执行而不比较新旧版本号。

双推完成后，工作流逐一核对 `docker/metadata-action` 产出的全部标签在两处 registry 解析出的远端 manifest digest，要求全部等于本次构建产出的 digest。只核对显式版本标签会放过 `latest` 或主版本标签在某一侧 registry 未成功更新的情况，而 `latest` 是 [docker-compose.yml](../../deploy/docker-compose.yml) 与 [生产 Compose 配置](../../deploy/production/docker-compose.yml) 的默认拉取入口；逐标签核对为双处同步全部落地提供直接验证。
