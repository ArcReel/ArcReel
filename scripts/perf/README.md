# 大项目性能基准

这个工具用合成的参考生视频项目测量重启后首开、空闲和生成高峰的聚合接口耗时。基准在进程内驱动 ASGI 应用，供开发者手动比较两份检出的表现。

## 当前运行状态

在空数据目录运行 `uv run python -m scripts.perf.bench all --rebuild` 会生成 schema 17 项目、从隔离数据库捕获升级前的推荐模型，并迁移至当前项目版本。报告中的延迟来自合成负载。

可在仓库根目录查看命令及参数。

```bash
uv run python -m scripts.perf.bench --help
uv run python -m scripts.perf.bench all --help
```

基准工具只接受空目录或由其自身创建的目录，默认路径为系统临时目录下的 `arcreel-perf-bench`。重建时会清空该目录下的 `projects`，并行运行时请通过 `--data-root` 分别指定独立目录。由于高峰模拟会追加新的视频版本，两次对比测量应从同一份初始项目开始。

## 测量与输出

[命令入口](bench.py)中的 `all` 子命令会依次完成项目构建、空闲请求测量和高峰模拟，并将结果写入数据目录下的 `out/`。覆盖接口包括工作台摘要（`workspace`）、旧项目详情（`project`）、费用估算（`cost`）、无参制作状态（`wf_status`）、指定集制作状态（`wf_ep_status`）、无参工作流规划（`wf_plan`）、指定集工作流规划（`wf_ep_plan`）、逐集下一步首窗（`wf_episodes`）和参考单元列表（`units`）。可通过 `--only` 选择子集以对照历史 5 接口基线或单独测量工作流 v3 与工作台入口。

| 指标 | 口径 |
|---|---|
| 重启后首开 | 每个接口在新进程中首次请求的耗时，排除模块导入和建表开销。 |
| 空闲 p95 | 热态下连续 `--reps` 次请求的 p95 耗时。 |
| 高峰 p95 | 高峰期服务端请求耗时的 p95，包含客户端已放弃的请求。排空超时后仍未完成的请求仅统计到截止时刻，此时汇总结果会将 p95 标为下限。 |
| 同时在算最大份数 | 已登记计算探针的接口统计计算函数的并发数，其余接口统计在途请求数。 |
| 排空时间 | 停止写入后等待在途请求完成的时间。达到上限时同时报告未完成请求。 |
| 轻量请求 p95 | 视频 Range 请求（`media_range`）和鉴权请求（`auth_verify`）的高峰期耗时。 |
| 启动迁移 | 合成项目从 v17 运行整条项目迁移链的耗时，以及迁移后带内容指纹的视频条目数。复用项目时沿用上次构建的数据。 |
| 热态读取媒体内容 | 热态打开项目详情时读取视频或音频内容的次数与字节数，排除仅读取 1 字节的可读性探测。 |

`summary-<tag>.md` 汇总上述指标。`idle-<tag>.json` 保留各轮空闲耗时和最后一次热请求的计数。`peak-<tag>.json` 保留高峰期各接口的汇总、线程池采样和清单锁指标，并单列未被客户端放弃的请求 p95（`delivered_p95`）。高峰模拟按[基准代码](bench.py)中的实体变更及任务终态两批请求模拟客户端重拉，以[提交帮助函数](harness.py)暂存并提交合成视频。

## 重放 #4045 合成基线

在 Linux 上运行。90 集默认每集 28 个单元、30 张资产图。指定 300 或 686 集时默认每集 19 个单元、400 张资产图。大项目在每条已选版本记录中写入提示词，使历史 JSON 接近 29 MB。各版本快照与正式文件独立存放。以下两份冻结报告使用测试参数 `--no-racy-wait`，在构建完成后跳过内容指纹等待窗口。

```bash
uv run python -m scripts.perf.bench all --episodes 300 --distribution mixed --duration 8 --reps 1 --video-kb 1 --sheet-kb 1 --no-racy-wait --rebuild --data-root /tmp/perf-300
uv run python -m scripts.perf.bench all --episodes 686 --distribution tail --ep 686 --only units --duration 8 --reps 1 --video-kb 1 --sheet-kb 1 --no-racy-wait --rebuild --data-root /tmp/perf-686
```

`mixed` 在大项目中每五集有一集按 `--video-ratio` 出片，其余集出齐视频并创建剪辑时间线。`complete` 完成全部集。`tail` 仅缺尾集最后一个单元。`stale` 在补录后修改偶数集首个正式视频，保留原快照。

每个接口另起进程记录请求前 RSS、10 ms 采样峰值与进程高水位（KiB）。`--tracemalloc` 增加独立轮次，记录 Python 分配峰值（字节）。采样轮次独立于首开和空闲延迟统计。JSON 读次数按项目、脚本和历史文件的只读打开次数计。版本写锁累计等待统计模拟视频提交持锁入口。生产观测及部署环境见 [Spec #4044](https://github.com/ArcReel/ArcReel/issues/4044)。

行为样本位于 [tests/fixtures/perf_before](../../tests/fixtures/perf_before)。运行 `uv run python -m pytest tests/integration/lib/project/test_perf_before.py tests/unit/lib/artifacts/test_json_history.py tests/integration/lib/artifacts/test_sqlite_history.py` 可检查工作流 v2 输出、旧项目升级、两种归档的导入及版本号分配。

[before 报告](before/)内的 `argv` 给出复跑命令。90 集使用默认的 240 秒高峰。300 集混合分布和 686 集尾集未完成分布使用 1 KiB 媒体、1 次热读和 8 秒高峰；前者覆盖五个接口，后者测量第 686 集单元列表，写入后的高峰重拉仍按写入集取数。300 集全完成与 stale 混合报告单测无参 `workflow-status`。各轮共用一台 4 核 Linux 主机，部分构建和测量时段重叠。比较时需同时查看请求数与排空时间，单样本 p95 等于本次耗时。

`observed_at_end` 记录测量结束时历史和清单的实际字节数、资产图文件数。`units_trace` 来自高峰结束后单独启动的单元列表请求。报告以 KiB 表示 RSS，以字节表示 Python 分配峰值；请求的 RSS 增量为 `sampled_peak_kib - baseline_kib`。
