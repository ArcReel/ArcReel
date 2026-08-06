# Ad 模式契约

本项目是广告 / 通用短片模式：恒单集，正式剧本为 `scripts/episode_1.json` 的 `shots[]`。没有小说导入、分集规划和 step1。

## 输入分支

- **带货**：用户诉求指向具体产品。产品必须登记并至少有原图；`selling_points` 在剧本生成前确认。
- **通用短片**：`products` 为空且诉求不指向具体产品。`brief` 是唯一创作输入，不索要产品信息。

## 状态顺序

| workflow state | 完成条件 | 动作 |
|---|---|---|
| `PROJECT_INPUT` | `brief`、`target_duration`、路线可用；带货产品原图齐备 | 对话补充后经 `patch_project` 写入 |
| `SELLING_POINTS` | 每个带货产品的卖点已确认 | 起草、确认并写入 products |
| `ASSET_SHEETS` | 已定义的角色 / 场景 / 道具 sheet current；没有定义则跳过 | dispatch `run-generation-task` |
| `FINAL_SCRIPT` | `shots[]` 剧本 current，时长接近目标 | dispatch `create-episode-script` |
| `PRODUCT_REVIEW` | 每个当前 product sheet revision 已明确审核；无 sheet 跳过 | 引导用户检查并确认 |
| `STORYBOARD` | storyboard 路线所有 shot 分镜 current | 生成并审核产品保真；reference 跳过 |
| `VIDEO` | 所有 shot / 派生 unit 视频 current | dispatch 视频生成 |
| `EXPORT_READY` | 核心产物 current | 引导 Web 端导出剪映草稿与字幕轨 |

## 路线规则

- storyboard：每个 shot 生成单图分镜，再图生视频；产品镜头自动注入产品参考。
- reference-video：服务端从连续 `shots[]` 派生 `reference_units`，直接生成视频；`shots[]` 始终是内容真相源。
- ad 不使用宫格，也不使用 `compose-video`；成片出口是 Web 端剪映草稿。

产品 sheet 审核是当前 revision 的硬质量门；换图或重生 sheet 后原确认自动失效。
