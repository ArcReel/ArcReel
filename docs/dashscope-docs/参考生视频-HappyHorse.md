# HappyHorse 参考生视频（happyhorse-1.0-r2v）

HappyHorse 参考生视频支持传入**多张参考图像**,通过**文本提示词**描述场景,将图像中的主体角色融合生成一段流畅视频。

通用 API 模式(异步、Headers、轮询)详见 [API 概览.md](./API%20概览.md)。本文只列模型独有 schema。

## 步骤 1:创建任务

```
POST /api/v1/services/aigc/video-generation/video-synthesis
```

### 请求体

```json
{
  "model": "happyhorse-1.0-r2v",
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

固定值 `happyhorse-1.0-r2v`。

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
| `resolution` | string | `1080P` | `720P` / `1080P` | 分辨率档位 |
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

## ArcReel 集成要点

- **R2V 单镜头参考图上限 = 9**(`max_reference_images: 9`)
- **resolutions**:`["720P", "1080P"]`
- **supported_durations**:`[3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]`
- **capabilities**:`["reference_images", "generate_audio"]`(音频恒开,无开关参数)
- **水印**:`watermark=false` 关闭,默认会带 "Happy Horse" 水印 — ArcReel 应在 backend 默认传 `false`

## 同系列模型(待官方扩充)

HappyHorse 系列除 R2V 外,根据 PRD 还规划:
- `happyhorse-1.0-t2v`(文生视频 + 音频)
- `happyhorse-1.0-i2v`(图生视频 + 音频)

这两个的 schema 阿里官方文档另列,字段大体同构(`input.prompt` + `parameters.resolution/duration` 等),实现时按官方文档为准。
