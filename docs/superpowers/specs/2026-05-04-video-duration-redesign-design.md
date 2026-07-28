# 视频时长（supported_durations）系统性重设计

**日期**：2026-05-04
**状态**：design 草案
**触发场景**：用户给自定义 provider 选 6s 分镜，对端返回 `seconds must be one of [6, 10, 12, 16, 20]`。根因是 `OpenAIVideoBackend._map_duration` 把 6 静默改成 8。深挖发现真相源虽已统一但被多处旁路。

---

## 1. 目标 & 非目标

### 目标

1. **单一真相源**：每个 model 一个 `supported_durations: list[int]`（离散集，连续区间整数全展开），永不空。
2. **三个消费点同源**：剧本生成 prompt / 前端时长按钮 / VideoBackend 请求体，都从同一条 resolver 输出消费。
3. **自定义 provider 创建模型**由 `model_id` 启发式预设表自动填充，未匹配回退 `[4, 8]`，UI 暴露逗号文本输入并支持 `3-15` 简写自动展开。
4. **删除一切静默篡改与隐性 fallback**：`VALID_DURATIONS`、prompt 与 script_generator 的 `or [...]` 默认、`_map_duration`、`_normalize_duration`。

### 非目标

- 不引入"用户输入任意秒数自由透传"模式（剧本生成需要 LLM 看到离散集）。
- 不在切 backend 时自动 snap/迁移历史 `duration_seconds`。
- 不改 `default_duration: int | null` 既有 "auto" 语义。
- 不引入 schema 级别的连续区间类型（用 list 全展开 + 前端检测连续性折中）。
- 不动 Veo 的 `duration_resolution_constraints`（720p/1080p 与时长组合限制），与本次重设计正交。
- 不动 reference_video 路径（`lib/reference_video/limits.py` 已正确消费 model.supported_durations）。

---

## 2. 真相源链路（最终态）

```
单一真相源（per-model）
├─ 内置：lib/config/registry.py::ModelInfo.supported_durations
└─ 自定义：CustomProviderModel.supported_durations (DB JSON list[int])
              ▲
              │ 创建/编辑模型时写入
              │ 来源 = 用户 Form 输入 OR 预设表 infer_supported_durations(model_id)
              │
ConfigResolver.video_capabilities()  → {supported_durations, max_duration, ...}
              │ 返回空 → ConfigError（不再 silent fallback）
              │
   ┌──────────┼──────────────────────────────────┐
   ▼          ▼                                  ▼
[剧本生成]   [前端时长选择器]                [视频生成请求]
prompt:     ModelConfigSection /             VideoBackend.generate
"从 [...]    SegmentCard                     原值透传：
中选择,      • 离散集 → 按钮组                 • OpenAI: kwargs["seconds"] = request.duration_seconds
默认 X 秒"   • 连续整数 → slider               • Veo: 删 _normalize_duration，原值透传
or "自由     越界历史值显示                   • Ark/Grok/NewAPI: 已是透传，不动
决定"        "⚠ Ns 不兼容" 角标               越界 → 对端 400 → 任务失败 + 错误回显前端

prompt builder 同样按"是否连续"切换文案
```

---

## 3. 预设表（新增单一文件）

新文件 `lib/custom_provider/duration_presets.py`：

```python
"""自定义供应商 model_id → supported_durations 启发式预设表。

数据来源：lmarena 视频模型排行榜 Top 20（2026-05 快照）+ 常见聚合命名。
匹配按 PRESETS 顺序，命中即返回；未匹配 → DEFAULT_FALLBACK。
受 tests/test_duration_presets.py 全分支覆盖约束。
"""

from __future__ import annotations
import re

DEFAULT_FALLBACK: list[int] = [4, 8]

# 按特异性从高到低排列，命中一条即返回。
# range 即 list(range(min, max+1)) 全展开为离散集。
PRESETS: list[tuple[re.Pattern[str], list[int]]] = [
    # OpenAI Sora 第一方
    (re.compile(r"^sora-2(-pro)?(-\d{4}-\d{2}-\d{2})?$", re.I), [4, 8, 12]),
    # 第三方聚合 Sora-2 变体（常见 6/10/12/16/20）
    (re.compile(r"sora.*pro", re.I), [6, 10, 12, 16, 20]),

    # Google Veo 系列（Vertex / Gemini API）
    (re.compile(r"veo-?\d", re.I), [4, 6, 8]),

    # Kling 全系（v1 / v2 / v2.5 / v2.6 / v3.0 / o1 / turbo / pro / omni / standard）
    (re.compile(r"kling[-.]?(o1|v?[123](\.\d+)?)", re.I), [5, 10]),

    # Runway Gen 系列
    (re.compile(r"^(runway[-.]?)?gen-?\d", re.I), [5, 8, 10]),

    # Luma Ray / Dream Machine
    (re.compile(r"\bray-?\d", re.I), [5, 10]),

    # ByteDance Dreamina / Seedance（4-15 任意）
    (re.compile(r"dreamina|seedance", re.I), list(range(4, 16))),

    # 字节即梦
    (re.compile(r"jimeng", re.I), list(range(4, 16))),

    # Alibaba HappyHorse（3-15 任意）
    (re.compile(r"happyhorse", re.I), list(range(3, 16))),

    # xAI Grok Imagine（1-15 任意）
    (re.compile(r"grok[-.]?imagine", re.I), list(range(1, 16))),

    # Vidu Q 系列（1-16 任意）
    (re.compile(r"vidu", re.I), list(range(1, 17))),

    # PixVerse V5/V5.5/V5.6/V6（1-15 任意）
    (re.compile(r"pixverse|^v[56](\.\d+)?$", re.I), list(range(1, 16))),

    # MiniMax Hailuo（固定 6）
    (re.compile(r"hailuo|minimax", re.I), [6]),

    # 阿里 Wan
    (re.compile(r"wan-?\d", re.I), [4, 5]),

    # Pika
    (re.compile(r"pika", re.I), [3, 5, 10]),
]


def infer_supported_durations(model_id: str) -> list[int]:
    """根据 model_id 启发式推导。未匹配 → DEFAULT_FALLBACK。

    返回值始终是非空升序去重的正整数列表（PRESETS 与 fallback 共同保证）。
    """
    for pattern, durations in PRESETS:
        if pattern.search(model_id):
            return list(durations)
    return list(DEFAULT_FALLBACK)
```

**歧义说明**：同名 model_id（如 `sora-2-pro`）在 OpenAI 第一方与第三方聚合站点的实际允许秒数可能不同（前者 [4,8,12]，后者可能 [6,10,12,16,20]）。预设表只是**启发**，给用户一个起点，不保证 100% 准确。**用户必须在创建/编辑模型时 review 输入框值，必要时按聚合实际限制调整。**Form 帮助文案需明确这点。

---

## 4. 数据模型变更

### 4.1 ORM 字段

| 表 / 字段 | 现状 | 设计 |
|---|---|---|
| `CustomProviderModel.supported_durations` | `Text` (JSON list[int])，nullable | 保持类型；**应用层不接受空**：API/Form 不允许写 null；resolver 读到 null 抛 ConfigError |
| `ModelInfo.supported_durations` | `list[int] = field(default_factory=list)` | 不变；resolver 读到空抛 ConfigError |

### 4.2 Alembic 迁移

新迁移 `alembic/versions/<rev>_backfill_custom_model_durations.py`：

```python
def upgrade():
    # 1. 扫描 custom_provider_model where endpoint media_type=video AND supported_durations IS NULL
    # 2. 为每行调 infer_supported_durations(model_id)（在迁移文件里 inline 复制 preset 表的快照，不 import）
    #    inline 复制是为了使迁移历史与未来代码改动解耦，不让一支历史迁移跟着 preset 表演进
    # 3. 写回 supported_durations 字段
def downgrade():
    # no-op：回填后保留，不破坏数据
```

**inline 复制原则**：迁移脚本内嵌当时的 PRESETS 快照，不 import `lib.custom_provider.duration_presets`，避免未来改预设表后回放历史迁移结果不同。

---

## 5. 后端改造清单

| 文件 | 改动 |
|---|---|
| `lib/video_backends/openai.py` | 删除 `_map_duration`；`generate()` 改 `"seconds": str(request.duration_seconds)`（保持 OpenAI SDK 现有 str 形态，去掉中间 {4,8,12} 桶映射）；同步更新 result.duration_seconds 回退分支为 int(kwargs["seconds"]) |
| `lib/video_backends/gemini.py` | 删除 `_normalize_duration`；`generate()` 改 `"duration_seconds": str(request.duration_seconds)`（保持 google-genai SDK 现有 str 形态，去掉中间 4/6/8 桶映射） |
| `lib/data_validator.py` | 删除 `VALID_DURATIONS = {4, 6, 8}`；`duration_seconds` 校验改为 `isinstance(int) and value > 0`；其余结构校验不变 |
| `lib/prompt_builders_script.py` | 删除 `or [4, 6, 8]` fallback；签名 `supported_durations: list[int]` 改为必填（无默认）；调用方传错由类型检查发现 |
| `lib/prompt_builders_script.py::_format_duration_constraint` | 检测连续性：`if sorted(list) == list(range(min, max+1)) and len(list) >= 5: 输出 "时长：{min} 到 {max} 秒间整数任选..."；else: 维持现状 "时长：从 [{durations}] 秒中选择..."` |
| `lib/script_generator.py` | 删除两处 `or [4, 8]` 二级 fallback；`_resolve_supported_durations` 拿不到就 `raise ConfigError`，不再回落 project.json |
| `lib/config/resolver.py` | `_resolve_video_capabilities_from_project` 中 `if not supported_durations: raise ValueError` 已存在，无改动；确保所有路径都走它 |
| `lib/custom_provider/duration_presets.py` | **新增**（§3 已展示） |
| `lib/custom_provider/discovery.py` | 模型发现回写 `CustomProviderModel` 时若 endpoint media_type=video 且 supported_durations 未提供，调 `infer_supported_durations(model_id)` 预填 |
| `server/routers/custom_providers.py::ModelCreate / ModelUpdate` | 接受 `supported_durations: list[int] \| None`：None 时由 server 调 preset；非 None 时直接用用户值（信任前端校验过的输入）；endpoint media_type=video 时若最终仍为空抛 422 |

---

## 6. 前端改造清单

| 文件 | 改动 |
|---|---|
| `frontend/src/types/custom-provider.ts` | `supported_durations: number[] \| null` 已有；`ModelCreate / ModelUpdate` payload 接受字段 |
| `frontend/src/components/pages/settings/CustomProviderForm.tsx` | 视频类 endpoint 编辑模型时显示"支持秒数"输入：单行文本框 + 解析逻辑见 §6.1；空值提交允许（server 用 preset 兜底，UI 提示"将自动按模型 id 推导"） |
| `frontend/src/components/pages/settings/CustomProviderDetail.tsx` | 模型卡片显示 supported_durations：连续区间显示 `"3-15s"`，离散显示 `"4, 8, 12s"` |
| `frontend/src/components/shared/ModelConfigSection.tsx` | 已按 supported_durations 渲染按钮；新增连续性检测：`if isContinuous(list) && list.length ≥ 5: 渲染 slider；else: 按钮组` |
| `frontend/src/components/canvas/timeline/SegmentCard.tsx` | 同样的连续性检测 + 形态切换；越界检测：`if !supportedDurations.includes(segment.duration_seconds): 在选中 chip 旁显示 ⚠ 角标 + tooltip "此时长不在当前模型支持列表中"` |
| `frontend/src/utils/duration_format.ts`（新） | 共享工具：`isContinuousIntegerRange(durations)`、`formatDurationsLabel(durations)`、`parseDurationInput(text)` |
| 前端 i18n keys（新增） | `dashboard:custom_provider.supported_durations_label` / `..._placeholder` / `..._help_inferred` / `..._help_format`；`dashboard:segment_card.duration_incompatible_warning` |

### 6.1 用户输入解析规则（`parseDurationInput`）

```
input: "3, 5, 7-10, 12"
解析步骤：
  1. 按 "," split
  2. 每段 trim
  3. 若匹配 ^\d+$ → 单值整数
  4. 若匹配 ^(\d+)-(\d+)$ → 展开为 list(range(min, max+1))，要求 max >= min 且 max - min ≤ 30
  5. 其他 → 抛 ParseError "无法解析片段 '{seg}'"
  6. 全部值合并、去重、升序、要求 ≥ 1 个正整数

output: [3, 5, 7, 8, 9, 10, 12]

显示回填（编辑场景）：
  list → 调用 compactRangeFormat(list) → 连续段折叠成 "min-max"，不连续保留单值
  例 [3,4,5,7,8,9,10,12] → "3-5, 7-10, 12"
```

### 6.2 连续性检测（`isContinuousIntegerRange`）

```ts
function isContinuousIntegerRange(durations: number[]): boolean {
  if (durations.length < 2) return false;
  const sorted = [...durations].sort((a, b) => a - b);
  return sorted.every((v, i) => i === 0 || v === sorted[i - 1] + 1);
}
```

UI 决策：`isContinuousIntegerRange(d) && d.length >= 5` → slider；否则按钮组。阈值 5 兼顾 `[4,5,6,7,8]` 这种小连续集仍想用按钮 / `[1..15]` 大连续集用 slider 的直觉。

---

## 7. 错误处理矩阵

| 触发点 | 现象 | 行为 |
|---|---|---|
| Resolver 找不到 supported_durations | 自定义 model 的 supported_durations 为空（迁移漏掉、人工写脏） | `raise ValueError("supported_durations is empty for {provider}/{model}")`；前端在生成路径上把异常 i18n 化为"模型配置异常" |
| 用户 Form 输入非法 | "abc, 4" / "10-3" | 前端 `parseDurationInput` 抛错，i18n 文案显示在输入框下方 |
| LLM 输出 duration 越界 | 剧本里出 7s 但 list 是 [4,6,8] | data_validator 不卡（已删 VALID_DURATIONS）；落库；最终视频生成时由对端 400 反馈 |
| 用户切 backend 后历史分镜越界 | 6s 在 [4,8,12] 模型下 | UI 角标提示；视频生成发对端 → 400 → 任务失败；错误信息含对端原文（i18n 包装 + 原文同时展示） |
| Backend 透传后对端 4xx | OpenAI 的 "seconds must be one of [...]" | 错误透出到 task.error_message；前端任务列表展开可见原文 |

---

## 8. 测试矩阵

### 8.1 后端

- `tests/test_duration_presets.py`（**新**）：每条 PRESETS 正则各一条命中样本；DEFAULT_FALLBACK 命中（未知 model id）；返回值确认为非空升序去重正整数。
- `tests/test_openai_video_backend.py`（改）：删除 `test_duration_mapping`；新增 `test_seconds_passthrough` —— seconds=6 → call_kwargs["seconds"] == 6；`test_video_seconds_none_fallback` 改为 seconds=6 透传时 result.duration_seconds=6。
- `tests/test_gemini_video_backend.py`（改）：seconds=7 透传，不被改成 8；删除标准化测试。
- `tests/test_data_validator.py`（改）：seconds=10 / 5 不再报错；非整数仍报错；duration_seconds=0 / 负数报错。
- `tests/test_resolver.py`（改/新）：`_resolve_video_capabilities_from_project` 在 supported_durations 为空时抛错；自定义 provider 路径覆盖。
- `tests/test_script_generator.py`（改）：删除 fallback 测试；caps=None 时 `_resolve_supported_durations` 抛错。
- `tests/test_alembic_supported_durations_backfill.py`（**新**）：升级前 NULL 行 → 升级后被回填为预设表对应值；inline preset 快照与当前 `duration_presets.py` 数据 drift 检测（warning 即可，不阻塞）。
- `tests/test_custom_providers_router.py`（改）：POST `/custom-providers/{id}/models` 不传 supported_durations 时 server 自动用 preset；传非空时直接用；endpoint=video 且最终空时返 422。

### 8.2 前端

- `frontend/src/utils/duration_format.test.ts`（**新**）：`parseDurationInput` 各种输入；`isContinuousIntegerRange` 边界；`compactRangeFormat` 往返一致。
- `frontend/src/components/pages/settings/CustomProviderForm.test.tsx`（新/扩）：编辑模型时输入 `3-15` → 提交 payload supported_durations.length === 13；非法输入显示错误；清空提交允许。
- `frontend/src/components/shared/ModelConfigSection.test.tsx`（改）：`supported_durations=[1..15]` 时渲染 slider；`[4,6,8]` 时按钮组。
- `frontend/src/components/canvas/timeline/SegmentCard.test.tsx`（扩）：`segment.duration_seconds=6` 但 `supportedDurations=[4,8,12]` 时显示 ⚠ 角标。

---

## 9. 升级与兼容性

- alembic 一次性回填 `CustomProviderModel.supported_durations` NULL 行（§4.2）。
- 已有 episode JSON 的 `duration_seconds=5/7/10` 等"非 {4,6,8}"值不再被 data_validator 拒绝（这是修复，不是回归）。
- 已有自定义 provider 模型若 endpoint=video 但因迁移漏跑导致 supported_durations 仍空：resolver 抛 ConfigError，UI 显示"模型配置异常"，用户进配置页编辑保存即由 preset 自动填。
- 视频生成失败的历史任务用户可手动改秒数后重发；不主动写历史数据。

---

## 10. Out of scope（明确不做）

- 视频生成时"自动找最近合法值并 snap"。
- 切 backend 时弹批量迁移对话框。
- 给每个秒数预估时长/费用（已有 cost_estimation 独立链路，本次不动）。
- Veo 的 `duration_resolution_constraints`（720p only at 8s 等组合限制）—— 现有逻辑保留，与本次重设计正交。
- discovery 端预设表自动应用到内置 `lib/config/registry.py`（registry 已是手维护，不动）。
- 区间/离散在 schema 层的显式区分（§5/§6 用前端检测连续性折中）。
