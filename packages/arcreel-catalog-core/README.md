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
  "context_length": 1048576,
  "facts": {"image_input": true, "structured_output": true, "max_output_tokens": 65536}
}
```

- 身份是「模型厂商/官方模型名」（`maker`/`name`），两栖模型的两个条目再以 `media_type` 区分。`maker` 不含 `/`。
- `aliases` 是模型别名，只用于把模型名对应到条目。同一媒体类型内，所有条目的官方模型名与模型别名按
  `normalize_model_name`（忽略大小写、去掉命名空间、点号视同连字符）归一化后不能重复。
- `discontinued` 表示已下架；`context_length` 是上下文长度（token）。
- `facts` 是模型事实，按媒体类型取不同字段：
  - `text`：`image_input`、`structured_output`、`max_output_tokens`；
  - `image`：`text_to_image`、`image_to_image`、`resolutions`（可选分辨率档位）；
  - `video`：`durations`（可选时长，必填）、`resolutions`、`duration_constraints`
    （`by_resolution` 按分辨率收窄可选时长，`with_reference_images` 是使用参考图时允许的时长）；
  - `audio`：无。
- `price` 是价目表，没有价格时省略。

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
  媒体类型与单价对应的计数量（token 类每百万、字符每万、其余每 1），`DIMENSIONS` 给出每个选择维度的取值形态。

### 内置供应商清单

```json
{
  "kling": {
    "models": [
      {"api_model_name": "kling-v3-omni", "entry": "kuaishou/kling-v3-omni", "media_type": "video"},
      {"api_model_name": "kling-v3-omni", "entry": "kuaishou/kling-v3-omni", "media_type": "image",
       "multiplier": 1.0, "min_app_version": "0.34.0"}
    ],
    "defaults": {"video": "kling-v3-omni", "image": "kling-v3-omni"}
  }
}
```

- 每个供应商模型给出 API 模型名、引用的条目（「模型厂商/官方模型名」）、供应商模型倍率（默认 1）与
  可选的最低应用版本。只有引用两栖模型时才需要 `media_type` 指明条目。
- 同一供应商下，供应商模型以 API 模型名与所引用条目的媒体类型区分。
- `defaults` 按媒体类型给出默认模型的 API 模型名，必须在该供应商这一媒体类型的清单中，且条目未下架。

## 两侧的判定

| | 实例侧（`parse_catalog`、`load_catalog`、`parse_entry`） | 构建侧（`check_catalog`、`check_catalog_files`、`check_entry`） |
|---|---|---|
| 不认识的字段 | 忽略 | 报告 |
| 不认识的计量项、选择维度、币种 | 照常读入，`PriceTable.is_priceable` 为 False，按未定价处理 | 报告 |
| 不认识的媒体类型 | 跳过该条目 | 报告 |
| 结构无法解析、哈希或数据集版本对不上 | 抛 `CatalogFormatError`，整份拒收 | 报告 |
| 条目身份重复、名字冲突、清单引用不存在的条目、价目表行重叠等 | 不检查 | 报告 |

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
