# HappyHorse 视频系列（1.0 / 1.1 的 t2v / i2v / r2v）

HappyHorse 是阿里自研的原生多模态视频生成系列,音画联合生成(输出恒带音轨,无音频开关参数)。1.0 与 1.1 两代并行在售,各含文生视频(t2v)、图生视频(i2v)、参考生视频(r2v)三个模态。

通用 API 模式(异步、Headers、轮询)详见 [API 概览.md](./API%20概览.md)。本文以参考生视频的完整 schema 为主线,t2v / i2v 的差异集中在文末[同系列模态](#同系列模态t2v--i2v)一节。1.1 相对 1.0 的差异见[版本差异](#版本差异11-vs-10)。

## 步骤 1:创建任务

```
POST /api/v1/services/aigc/video-generation/video-synthesis
```

### 请求体

```json
{
  "model": "happyhorse-1.1-r2v",
  "input": {
    "prompt": "[Image 1]中身着红色旗袍的女性,镜头先以侧面中景勾勒...",
    "media": [
      {"type": "reference_image", "url": "https://.../girl.jpg"},
      {"type": "reference_image", "url": "https://.../fan.jpg"},
      {"type": "reference_image", "url": "https://.../earrings.jpg"}
    ]
  },
  "parameters": {
    "resolution": "720P",
    "ratio": "16:9",
    "duration": 5
  }
}
```

### `model`

`happyhorse-1.1-r2v` 或 `happyhorse-1.0-r2v`。

### `input.prompt`（必选)

- 类型:`string`
- 长度:**英文 ≤ 5000 字符 / 中文 ≤ 2500 字符**,超出自动截断
- 参考指代:用 `[Image 1]`、`[Image 2]` ... 标识对应 `media` 数组顺序
  - 例:"[Image 1]中身着红色旗袍的女性" — 必须指明参考图的具体对象,不能只写 "[Image 1]"

### `input.media`（必选)

类型:`array`,每个元素是 `{type, url}` 对象。

| 字段 | 必选 | 说明 |
|------|------|------|
| `type` | 是 | 固定 `reference_image` |
| `url` | 是 | 图像 URL(http/https)或 Base64 `data:{MIME};base64,{data}` |

**约束**:
- 参考图数量:**1 ~ 9 张**
- 图像格式:JPEG / JPG / PNG / WEBP
- 分辨率:短边 ≥ 400 px(推荐 720P+;过小/模糊/压缩重的影响效果)
- 文件大小:≤ 20 MB

### `parameters`（可选)

| 参数 | 类型 | 默认 | 取值 | 说明 |
|------|------|------|------|------|
| `resolution` | string | `1080P` | `480P` / `720P` / `1080P` | 分辨率档位。官方参数表为 1.1 / 1.0 合并呈现,480P 的版本归属见[版本差异](#版本差异11-vs-10) |
| `ratio` | string | `16:9` | `16:9` / `9:16` / `3:4` / `4:3` / `4:5` / `5:4` / `1:1` / `9:21` / `21:9` | 宽高比 |
| `duration` | integer | `5` | `3 ~ 15` 整数(秒) | 视频时长,按秒计费 |
| `watermark` | bool | `true` | `true` / `false` | 右下角水印,文案固定 "Happy Horse" |
| `seed` | integer | 随机 | `[0, 2147483647]` | 固定种子提升可复现性(但不保证完全一致) |

## 步骤 2:轮询任务结果

```
GET /api/v1/tasks/{task_id}
```

### 成功响应

```json
{
  "request_id": "35137489-...",
  "output": {
    "task_id": "1469cfc3-...",
    "task_status": "SUCCEEDED",
    "submit_time":    "2026-04-25 15:03:25.848",
    "scheduled_time": "2026-04-25 15:03:25.884",
    "end_time":       "2026-04-25 15:04:05.882",
    "orig_prompt": "...",
    "video_url": "https://dashscope-result.oss-cn-beijing.aliyuncs.com/xxxx.mp4"
  },
  "usage": {
    "duration": 5,
    "input_video_duration": 0,
    "output_video_duration": 5,
    "video_count": 1,
    "SR": 720,
    "ratio": "16:9"
  }
}
```

### `usage` 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `duration` | int | **总视频时长(秒),用于计费** |
| `input_video_duration` | int | 输入视频总时长 — 参考生视频中**固定为 0** |
| `output_video_duration` | int | 输出视频时长 |
| `video_count` | int | 生成视频数量,固定为 1 |
| `SR` | int | 输出分辨率档位(如 720) |
| `ratio` | string | 输出宽高比 |

## 同系列模态（t2v / i2v）

三个模态共用同一异步端点与 `parameters` 表(`resolution` / `duration` / `watermark` / `seed`),差异只在输入形态与 `ratio`:

| 模态 | model id | 输入 | `ratio` |
|------|----------|------|---------|
| 文生视频 | `happyhorse-1.1-t2v` / `happyhorse-1.0-t2v` | 仅 `input.prompt` | 支持,9 档(默认 `16:9`) |
| 图生视频 | `happyhorse-1.1-i2v` / `happyhorse-1.0-i2v` | 首帧图(`media` 中 `first_frame`) | **不支持**,宽高比自动跟随首帧 |
| 参考生视频 | `happyhorse-1.1-r2v` / `happyhorse-1.0-r2v` | 参考图 1~9 张 | 支持,同 t2v |

图生视频首帧图约束:JPEG / JPG / PNG / WEBP;宽高均 ≥ 300 px;宽高比 1:2.5 ~ 2.5:1;≤ 20 MB。全系**无尾帧**能力,首尾帧场景走 wan2.7 系列(见 [参考生视频-wan2.7.md](./参考生视频-wan2.7.md))。

## 版本差异（1.1 vs 1.0）

- 分辨率:官方参数表把 1.1 / 1.0 合并呈现为 `480P` / `720P` / `1080P`,未按版本区分;1.0 早期资料只出现 `720P` / `1080P`,**480P 是否 1.0 通用未能确认**
- 定价(元/秒,刊例价):1.1 为 480P 0.45 / 720P 0.9 / 1080P 1.2;1.0 为 720P 0.9 / 1080P 1.6。详见 [阿里百炼费用参考.md](./阿里百炼费用参考.md)
- 1.1 指令遵循更严格、参考图还原度与口型同步精度提升、出片更快(来源为阿里云开发者社区通稿,非 API 参考);两代时长档位相同(3~15s)
- 1.0 全系仍在售,无官方停售公告

## ArcReel 集成要点

- **R2V 单镜头参考图上限 = 9**(`max_reference_images: 9`),t2v / i2v 无参考图槽位
- **能力位归 backend**:i2v 声明 `first_frame=True`,t2v / r2v 为 `False`(视频能力位不入 `ModelInfo.capabilities`,见 `docs/adr/0054`)
- **resolutions**:1.1 `["480p", "720p", "1080p"]`;1.0 `["720p", "1080p"]`(480P 对 1.0 未确权,registry 不替官方补写)
- **supported_durations**:`[3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]`(两代同)
- **音频**:恒开、无开关参数,registry 逐型号声明 `audio_always_on=True` 判定有音轨
- **水印**:官方默认 `watermark=true`(右下角 "Happy Horse"),ArcReel backend 构造 payload 时显式传 `false`
- **默认视频模型**:`happyhorse-1.1-i2v`
