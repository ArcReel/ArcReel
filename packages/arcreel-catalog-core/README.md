# arcreel-catalog-core

ArcReel 模型目录数据的格式定义与校验器。ArcReel 应用加载出厂快照与官方同步数据、目录数据仓库的 CI
共用这一份实现，同一份数据在两处得到同样的判定。本包只依赖标准库，不依赖 ArcReel 应用的任何部分。

术语见主仓 `GLOSSARY.md`「供应商与模型」「计费」两节，决策见 `docs/adr/0099`、`0100`、`0103`–`0105`。

## 文件

目录数据仓库发布两个文件：

| 文件 | 内容 |
|---|---|
| `manifest.json` | 清单文件：`dataset_version`（数据集版本）与 `sha256`（数据文件的 SHA-256，小写十六进制） |
| `catalog.json` | 数据文件：`dataset_version`、`entries`（模型条目）与 `builtin_providers`（内置供应商清单） |

数据集版本是 UTC 时间戳，格式 `YYYY-MM-DDTHH:MM:SSZ`，字符串顺序即新旧顺序。格式不设 schema 版本号。

### 模型条目

```json
{
  "maker": "google",
  "name": "gemini-3.1-pro",
  "media_type": "text",
  "display_name": "Gemini 3.1 Pro",
  "aliases": ["gemini-3.1-pro-latest"],
  "discontinued": false,
  "release_date": "2026-02-19",
  "shutdown_date": "2027-03-31",
  "successor": "google/gemini-3.5-pro",
  "context_length": 1048576,
  "facts": {"image_input": true, "structured_output": true, "max_output_tokens": 65536}
}
```

- 身份是「模型厂商/官方模型名」（`maker`/`name`），两栖模型的两个条目再以 `media_type` 区分。`maker` 不含 `/`。
- `aliases` 是模型别名，只用于把模型名对应到条目。同一媒体类型内，所有条目的官方模型名与模型别名按
  `normalize_model_name`（忽略大小写、去掉命名空间、点号视同连字符）归一化后不能重复。
- `discontinued` 表示已下线；`context_length` 是上下文长度（token）。
- 生命周期字段都可选，缺失表示未收录，写出时省略：
  - `release_date`、`shutdown_date` 是上线日期与关停日期，写作 `YYYY-MM-DD`，精确到日。关停日期按 UTC
    日期判断，关停当天即视为已下线；目录构建在关停日期不晚于构建当天时置真 `discontinued`，不认识关停日期的
    旧版实例据此认出已下线的模型。
  - `successor` 是厂商官方指定的后继模型，写作「模型厂商/官方模型名」，指向媒体类型与本条目相同的条目，
    所以引用两栖模型时不另写媒体类型。只记直接后继。
  - 生命周期表示模型厂商层面的退役，对所有供应商（包括自定义供应商）都适用。
- `facts` 是模型事实，按媒体类型取不同字段：
  - `text`：`image_input`、`structured_output`、`max_output_tokens`；
  - `image`：`text_to_image`、`image_to_image`、`resolutions`（可选分辨率档位），以及可选的 `max_reference_images`、`output_token_bases`；
  - `video`：`durations`（可选时长，必填）、`resolutions`、`duration_constraints`
    （`by_resolution` 按分辨率收窄可选时长，`with_reference_images` 是使用参考图时允许的时长），以及可选的 `max_reference_images`、`text_to_video`、`first_frame`、`last_frame`、`reference_audio`、`max_reference_audio_count`、`max_reference_audio_total_seconds`；
  - `audio`：无。
- `price` 是价目表，没有价格时省略。

图片和视频中标为「可选的」模型事实字段缺失表示未收录，写出时仍省略，不另设「未知」取值：

- `max_reference_images` 是非负整数，`0` 表示参考图数量上限为零，不表示无限制。图片是否接受图生图由 `image_to_image` 表达；视频首帧、尾帧是独立输入槽位。
- 视频的 `text_to_video`、`first_frame`、`last_frame`、`reference_audio` 是可选布尔值，分别表示是否接受文生视频、首帧、尾帧、参考音频。显式 `false` 与缺失不同。
- `max_reference_audio_count` 是参考音频数量上限（非负整数）；`max_reference_audio_total_seconds` 是多段参考音频合计时长上限（非负有限数，单位为秒）。显式 `0` 与缺失不同。`reference_audio` 为 `true` 时，`max_reference_audio_count` 不能为 `0`。
- 图片的 `output_token_bases` 是质量档到正整数估算基数的映射，例如 `{"low": 16, "medium": 48, "high": 96}`；未收录时为空映射，写出时省略。分辨率档到请求 `quality` 的映射属于端点代码，不记在条目上。

### 价目表

```json
{
  "currency": "CNY",
  "meters": [
    {
      "meter": "output_seconds",
      "dims": ["resolution", "audio"],
      "rows": [
        {"when": {"resolution": "720p", "audio": false}, "price": 0.6},
        {"when": {"resolution": "1080p"}, "price": 1.2}
      ],
      "defaults": {"resolution": "720p"}
    },
    {"meter": "input_images", "dims": [], "rows": [{"when": {}, "price": 0.1}], "free_allowance": 2}
  ]
}
```

- `currency` 取 `USD` 或 `CNY`，以模型厂商主站的币种标价。
- 每个计量项（`meter`）只出现一次，按 `dims` 列出的选择维度分档，每行是「条件 → 单价」，`when`
  缺省的维度表示任意取值。同一请求最多命中一行。
- `defaults` 是请求缺少某维度取值时使用的取值；`free_allowance` 是单请求免费额度，按计量项的计数单位计；
  `cjk_double` 只用于 `characters`，表示汉字计 2 个字符。
- 计量项与选择维度是封闭集合，定义在 `arcreel_catalog_core.vocabulary`：`METERS` 给出每个计量项适用的
  媒体类型、单价对应的计数量（token 类每百万、字符每万、其余每 1）与类别，`DIMENSIONS` 给出每个选择维度的取值形态，`DIMENSION_MEDIA_TYPES` 给出每个选择维度适用的媒体类型
  （编辑价目表时的引导，不是格式约束）。
- 计量项分两类。token 类（如未缓存输入、缓存读）是同一份 token 总量拆出的各部分，调用上报了某一项、价目表却
  没有它时，整笔记为未定价。计数类（秒数、视频 token、张数、字符数、调用次数等）各是一条独立的计费轴，价目表
  没有某一项就表示厂商不按它收费。

### 内置供应商清单

```json
{
  "kling": {
    "models": [
      {"api_model_name": "kling-v3-omni", "entry": "kuaishou/kling-v3-omni", "media_type": "video",
       "endpoint": "kling-video-audio"},
      {"api_model_name": "kling-v3-omni", "entry": "kuaishou/kling-v3-omni", "media_type": "image",
       "multiplier": 1.0, "min_app_version": "0.34.0", "endpoint": "kling-image"}
    ],
    "defaults": {"video": "kling-v3-omni", "image": "kling-v3-omni"}
  }
}
```

- 每个供应商模型给出 API 模型名、引用的条目（「模型厂商/官方模型名」）、供应商模型倍率（默认 1）与
  可选的最低应用版本。只有引用两栖模型时才需要 `media_type` 指明条目。
- `endpoint` 是供应商模型挂接的内置调用端点，发布数据必填。它必须在端点清单里，媒体类型与所引用的条目相同，且是该供应商在这一媒体类型下的可选端点。实例侧仍兼容缺失字段，缺失时取供应商默认端点。
- 同一供应商下，供应商模型以 API 模型名与所引用条目的媒体类型区分。
- `defaults` 按媒体类型给出默认模型的 API 模型名，必须在该供应商这一媒体类型的清单中，且条目既未下线，也没有关停日期。

### 端点清单

端点清单定义在 `arcreel_catalog_core.endpoints`，是代码里调用端点的一份副本，主仓用测试保证二者一致：

- `BUILTIN_ENDPOINTS`：内置供应商可挂接的每个内置调用端点的媒体类型与可选的起始版本 `since`。清单首次发布时已有的
  端点都是基线端点，不带起始版本。
- `BUILTIN_PROVIDER_ENDPOINTS`：每个内置供应商按媒体类型列出的可选端点与默认端点。
- `ENDPOINT_DECLARATIONS_SINCE`：实例开始读取端点声明的应用版本。

`endpoint_version_floor(供应商, 媒体类型, 端点)` 给出端点决定的最低应用版本下限：取端点的起始版本，以及该供应商在这一
媒体类型下有多个可选端点时实例开始读取端点声明的版本，两者中较高的那个。目录构建再与手写的最低应用版本取较高者。
已逐条核验的首发模型传 `include_declaration_version=False`，仅豁免声明读取版本，仍计入端点起始版本。
`app_version_key(版本)` 返回用于 `max(..., key=app_version_key)` 的数字比较键，省略的次版本、补丁号按 0 计；格式不合法时抛 `ValueError`。
推导规则与起始版本的填写方式见主仓 `docs/adr/0103`。

## 两侧的判定

| | 实例侧（`parse_catalog`、`load_catalog`、`parse_entry`） | 构建侧（`check_catalog`、`check_catalog_files`、`check_entry`） |
|---|---|---|
| 不认识的字段 | 忽略 | 报告 |
| 不认识的计量项、选择维度、币种 | 照常读入，`PriceTable.is_priceable` 为 False，按未定价处理 | 报告 |
| 不认识的媒体类型 | 跳过该条目 | 报告 |
| 结构无法解析、哈希或数据集版本对不上 | 抛 `CatalogFormatError`，整份拒收 | 报告 |
| 条目身份重复、名字冲突、清单引用不存在的条目、后继模型不存在或媒体类型不同、默认模型带关停日期、价目表行重叠等 | 不检查 | 报告 |
| 端点缺失、端点不在端点清单里、端点媒体类型与条目不同、端点不是该供应商的可选端点 | 照常读入 | 报告 |

问题以 `CatalogIssue(path, code, detail)` 给出，`code` 是稳定的比较依据。

## 使用

```bash
python -m arcreel_catalog_core check <目录>           # 校验目录下的 manifest.json 与 catalog.json
python -m arcreel_catalog_core write-manifest <目录>  # 按 catalog.json 写出 manifest.json
```

退出码非零即不通过，问题逐行输出为 `位置: [code] 说明`。

```python
from arcreel_catalog_core import check_catalog_files, load_catalog

issues = check_catalog_files(manifest_bytes, catalog_bytes)  # 构建侧
data = load_catalog(manifest_bytes, catalog_bytes)  # 实例侧
```

## 开发

本包是 ArcReel 仓库 uv workspace 的成员，测试在本目录的 `tests/` 下，与应用测试分开运行：

```bash
cd packages/arcreel-catalog-core && uv run python -m pytest
```
