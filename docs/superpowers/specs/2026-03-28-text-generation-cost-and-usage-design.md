# 文本生成费用计算与使用记录

> GitHub Issue: ArcReel/ArcReel#169
> 日期: 2026-03-28

## 背景

#168 完成通用文本生成服务层提取后，文本生成（小说总结、剧本生成、风格分析）支持多供应商调用，但这些调用未纳入费用计算和使用记录。本设计将文本生成的用量追踪集成到现有体系中。

## 设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| token 字段 | 新增 `input_tokens` + `output_tokens`，保留 `usage_tokens` | 文本生成 input/output 定价不同；`usage_tokens` 仅 Ark 视频在用，不破坏现有数据 |
| 集成层 | 创建 `TextGenerator` 包装层 | 与 `MediaGenerator` 模式一致，集中管理，调用方无需关心 tracking |
| project_name | 可选参数 | 未来可能有非项目级别的工具箱功能 |
| call_type | 统一 `"text"` | 与 image/video 平级，不按任务细分；未来需要可加 `task_type` 正交字段 |
| 前端展示 | 绿色 FileText 图标，token 信息替代分辨率/时长 | 与现有 image（蓝色）/ video（紫色）视觉体系一致 |

## 改动范围

### 1. 数据库层

#### ApiCall 模型新增字段

```python
# lib/db/models/api_call.py
input_tokens: Mapped[int | None] = mapped_column(default=None)
output_tokens: Mapped[int | None] = mapped_column(default=None)
```

- `usage_tokens` 保留不动（Ark 视频继续使用）
- `call_type` 新增 `"text"` 值（与 `"image"` / `"video"` 并列）
- Alembic 迁移：`ALTER TABLE api_calls ADD COLUMN input_tokens INTEGER, ADD COLUMN output_tokens INTEGER`

#### UsageRepository 改动

**`start_call()`**：`call_type` 接受 `"text"`（token 数在生成前未知，不在此处传入）。

**`finish_call()`**：新增 `input_tokens` / `output_tokens` 可选参数，增加 text 成本计算分支：

```python
if call.call_type == "text" and call.input_tokens is not None:
    amount, currency = cost_calculator.calculate_text_cost(
        input_tokens=call.input_tokens,
        output_tokens=call.output_tokens or 0,
        provider=call.provider,
        model=call.model,
    )
    call.cost_amount = amount
    call.currency = currency
```

**`get_stats()`**：返回值增加 `text_count` 字段。

### 2. TextGenerator 包装层

新增 `lib/text_generator.py`：

```python
class TextGenerator:
    """组合 TextBackend + UsageTracker，统一封装文本生成 + 用量追踪。"""

    def __init__(self, backend: TextBackend, usage_tracker: UsageTracker):
        self.backend = backend
        self.usage_tracker = usage_tracker

    @classmethod
    async def create(
        cls, task_type: TextTaskType, project_name: str | None = None
    ) -> "TextGenerator":
        backend = await create_text_backend_for_task(task_type, project_name)
        usage_tracker = UsageTracker()
        return cls(backend, usage_tracker)

    async def generate(
        self,
        request: TextGenerationRequest,
        project_name: str | None = None,
    ) -> TextGenerationResult:
        call_id = await self.usage_tracker.start_call(
            project_name=project_name,
            call_type="text",
            model=self.backend.model,
            prompt=request.prompt[:500],
            provider=self.backend.provider,
        )
        try:
            result = await self.backend.generate_async(request)
            await self.usage_tracker.finish_call(
                call_id=call_id,
                status="success",
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
            return result
        except Exception as e:
            await self.usage_tracker.finish_call(
                call_id=call_id,
                status="failed",
                error_message=str(e)[:500],
            )
            raise
```

设计要点：
- `backend.model` / `backend.provider`：三个 TextBackend 实现都已有这两个属性
- `project_name` 在 `generate()` 时传入（可选），而非构造时绑定
- 不引入 VersionManager——文本生成没有文件产出

### 3. 调用点改造（3 处）

| 调用点 | 文件 | 改前 | 改后 |
|--------|------|------|------|
| ScriptGenerator | `lib/script_generator.py` | `create_text_backend_for_task()` → `backend.generate_async()` | `TextGenerator.create()` → `generator.generate(request, project_name)` |
| ProjectManager.generate_overview | `lib/project_manager.py:1579` | 同上 | 同上 |
| 风格分析 | `server/routers/files.py:524` | 同上 | 同上 |

### 4. 前端改动

#### 类型扩展

```typescript
// UsageStats 增加
text_count: number;

// UsageCall 扩展
call_type: "image" | "video" | "text";
input_tokens: number | null;
output_tokens: number | null;
```

#### UsageDrawer

- 文本类型图标：绿色 `<FileText className="h-3.5 w-3.5 text-green-400" />`
- 列表行第二行：文本显示 token 信息（如 `输入 1,234 · 输出 5,678 tokens`），替代图片/视频的分辨率+时长
- 统计摘要增加文本调用数

#### UsageStatsSection

- `call_type="text"` 的统计卡片随分组数据自然出现（无需额外逻辑改动）
- 卡片中对文本类型显示 token 总数代替时长

#### GlobalHeader 成本徽章

- 无需改动——已基于 `cost_by_currency` 聚合，text 类型的费用自动纳入

## 不做的事

- 不纳入 GenerationQueue 任务队列——文本生成频次低，保持直接调用
- 不细分 `call_type`（如 `text_script` / `text_overview`）——统一 `"text"` 即可
- 不迁移现有 `usage_tokens` 数据——Ark 视频继续使用该字段
- 不新增 `task_type` 字段——当前无需求，未来按需添加
