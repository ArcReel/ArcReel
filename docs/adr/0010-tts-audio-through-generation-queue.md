---
status: proposed
---

# TTS（audio 媒体类型）走 GenerationQueue/Worker，像 image/video；backend 仍同步、不像内联的 text

ArcReel 的媒体生成沿 `media_type` 轴扇出：image/video 走 **GenerationQueue + GenerationWorker**（按 provider×media_type 分 slot，带进度/取消/续传/孤儿处理），text 则是 **同步内联调用**（`TextGenerator` = TextBackend + UsageTracker，不入队、worker 只 `for media_type in ("image","video")`、不建 task）。接入旁白配音（TTS）时第一个分叉是：audio 跟 text 走（同步内联）还是跟 image/video 走（队列）。

最初按"和文本调用一样、不做并发控制"倾向同步内联，理由是 TTS 后端调用本身**就是同步一次性 POST**（仿 `text_backends`，无提交-轮询，秒回），看上去更接近 text。但随后确认 **批量生成是 web 与 agent 双侧刚需**，且关键事实是：**旁白音频的生成基数（每 segment 一段、每集 N 段、可批量、可重生）与 image/video 一致，而非 text 的"每集一次"**。同步内联下的批量只能靠前端串行编排，进度绑在浏览器 tab、无任务面板、不能取消/续传/跨设备，与图片/视频的批量体验割裂；而队列正是为"批量长任务 + 进度 + 取消 + 续传"而生，图片/视频已在用。

我们决定 **audio 走 GenerationQueue/Worker，与 image/video 一致**：单段=入队一条、批量=入队 N 条，复用现有 `/api/v1/tasks` 任务面板。同时保留一个**刻意的非对称**——audio 的 **backend 仍是同步一次性**（仿 `text_backends`，非 video 的 submit→poll→resume），worker claim 后直接调同步 backend、秒回即标终态。也就是说：**"是否入队"由生成基数（是否按 segment 批量）决定，而非由 backend 是否异步决定**——这是未来读者会困惑的点（"audio 后端和 text 一样同步，为什么 audio 入队而 text 不入队？"），故记此 ADR。

## Considered Options

- **同步内联（像 text）+ 前端串行批量** —— 最少代码、worker 不动。否决：批量进度绑浏览器 tab、无任务面板、不可取消/续传/跨设备，与 image/video 割裂；且 audio 生成基数像 image/video 而非 text，类比不成立。
- **走队列（像 image/video）** —— 采纳。

## Consequences

- **worker 增第三条 lane**：`ProviderPool` 加 `audio_max` / `has_audio_room`，claim 循环 `for media_type in ("image","video")` 扩到含 `"audio"`，并触及 `_resolve_dispatch_provider` / `_any_pool_has_room` / `_pool_full_providers` / `_load_pools_from_db` / `_build_default_pools` 等 lane 相关点（机械改动）。TTS 便宜快，`AUDIO_MAX_WORKERS` 默认放宽，lane 不应成为瓶颈。
- **agent 工具是 `enqueue_tts(segment_ids?)`**（入队，仿 `enqueue_storyboards`），而非同步 `generate_*`；不传=所有缺失段、传 list=指定批量范围、传单个=单段。
- **web 入队 + 复用现有任务面板**：单段与批量都 enqueue，前端轮询 `/api/v1/tasks` 看进度，复用既有取消/续传 UI。
- **audio backend 保持同步、无 resume/`provider_job_id`**：worker claim → 调同步 backend（秒回）→ 标终态。`docs/adr/0007` 的孤儿处理对 audio 退化为"标 failed、不续传"，因同步且重生成廉价，无需 video 那套 submit-poll-resume 机制。
- **"同步"是针对"短 segment + 同步 API"的选择，异步是预留扩展点**：v1 选同步因为（a）所选供应商的 TTS API 本就同步返回字节（DashScope Qwen-TTS sync HTTP、OpenAI 兼容 `/v1/audio/speech` 立即返回），（b）按 segment 的旁白短（`duration_seconds ≤ 60`、文本有界）秒级完成。**但长文本 TTS 接口业界是异步的**（MiniMax T2A async、豆包异步长文本 ≤10万字、Google long-audio LRO、Azure batch）。若未来接入只提供异步 API 的供应商，或改为"整集一次性合成长文本"，需要 video 式 submit-poll 生命周期——故 `AudioBackend` Protocol 设计上**预留**异步可能（如 text/video 那样允许各自的 backend 形态），但 v1 只建同步。
- **Task 无需迁移**：`task.task_type` / `media_type` 是自由 String 列，audio 行直接落库。
- **版本化不变**：audio 与 image/video 一样经 VersionManager 落版本（见 `CONTEXT.md`「旁白配音」与 audio 媒体类型词条）。
- **与 text 的非对称是契约**：text 生成每集一次、同步内联不入队；audio 每集 N 段、入队。任何后续 PR 若想把 audio 改回同步内联（或把 text 改成入队），须先 deprecate 本 ADR 并说明生成基数的变化理由。
