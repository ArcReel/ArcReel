# 万相 2.7 参考生视频（wan2.7-r2v）

万相参考生视频(wan2.7-r2v)支持**多模态输入**(图像 / 视频 / 音色),可将人或物体作为主角,生成单角色表演或多角色互动视频。

通用 API 模式(异步、Headers、轮询)详见 [API 概览.md](./API%20概览.md)。本文只列模型独有 schema。

> **重要**:此接口为**新版协议**,仅支持 wan2.7 系列。wan2.6 是旧协议,字段名不同(见末尾「与 wan2.6 差异」)。

## 步骤 1:创建任务

```
POST /api/v1/services/aigc/video-generation/video-synthesis
```

### 请求体(多主体参考:图像 + 视频 + 音色)

```json
{
  "model": "wan2.7-r2v",
  "input": {
    "prompt": "视频1抱着图3,在图4的椅子上弹奏一支舒缓的乡村民谣...",
    "media": [
      {
        "type": "reference_image",
        "url": "https://.../girl.jpg",
        "reference_voice": "https://.../girl-voice.mp3"
      },
      {
        "type": "reference_video",
        "url": "https://.../role2.mp4",
        "reference_voice": "https://.../boy-voice.mp3"
      },
      {"type": "reference_image", "url": "https://.../object3.png"},
      {"type": "reference_image", "url": "https://.../object4.png"},
      {"type": "reference_image", "url": "https://.../background5.png"}
    ]
  },
  "parameters": {
    "resolution": "720P",
    "ratio": "16:9",
    "duration": 10,
    "prompt_extend": false,
    "watermark": true
  }
}
```

### 请求体(单图多宫格参考:故事板图像)

```json
{
  "model": "wan2.7-r2v",
  "input": {
    "prompt": "参考图片,3D 卡通冒险电影风,角色 Q 版... 分镜脚本:1. 全景...",
    "media": [
      {"type": "reference_image", "url": "https://.../storyboard_9grid.png"}
    ]
  },
  "parameters": {
    "resolution": "720P",
    "duration": 10,
    "prompt_extend": false,
    "watermark": true
  }
}
```

### `model`

固定值 `wan2.7-r2v`。

### `input.prompt`（必选)

- 类型:`string`
- 长度:wan2.7-r2v ≤ 5000 字符,超出自动截断(中英文混计,标点占 1 字符)
- 参考指代:
  - 中文 prompt:**图1 / 图2 ...** 指代图像;**视频1 / 视频2 ...** 指代视频
  - 英文 prompt:**Image 1 / Image 2 ...** 指代图像;**Video 1 / Video 2 ...** 指代视频
  - 图与视频**分别计数**(可同时存在「图1」与「视频1」)
  - 若参考素材只有一张图或一段视频,可简写为「参考图片」/「参考视频」
- 画面描述:
  - 直接指代:"图1在图2里玩耍"
  - 结合主体补充:"图1的猫在图2的房间里玩耍"
- 多宫格图像专用:按多分镜形式描述画面内容,模型自动识别宫格逻辑并补全镜头(建议单次仅传 1 张多宫格图)

### `input.negative_prompt`（可选)

- 类型:`string`
- 长度:≤ 500 字符
- 例:"低分辨率、错误、最差质量、低质量、残缺、多余的手指、比例不良等"

### `input.media`（必选)

类型:`array`,每个元素含 `type` / `url` / 可选 `reference_voice`。

#### `type` 枚举

| 值 | 说明 |
|----|------|
| `reference_image` | 参考图像(主体或场景;含主体时仅一个角色) |
| `reference_video` | 参考视频(提供主体角色和音色参考;不建议传空镜视频) |
| `first_frame` | 首帧图像(基于首帧生成视频) |

#### 素材数量约束

- `first_frame`:**最多 1 张**
- `reference_image + reference_video`:**至少 1 个,且总数 ≤ 5**
- 参考素材为主体角色时,**每张/每段仅含单一角色**

#### `url` 输入格式

- 公网 URL:`https://...` / `http://...`
- 临时 URL(OSS 协议):`oss://dashscope-instant/xxx/xxx.png`(需通过[上传文件获取临时 URL](https://help.aliyun.com/zh/model-studio/get-temporary-file-url))
- Base64:`data:{MIME};base64,{data}`

#### 图像约束(`reference_image` / `first_frame`)

- 格式:JPEG / JPG / **PNG**(不支持透明通道)/ BMP / WEBP
- 分辨率:宽高 ∈ `[240, 8000]` px
- 宽高比:`1:8 ~ 8:1`
- 文件大小:≤ 20 MB

#### 视频约束(`reference_video`)

- 格式:`mp4` / `mov`
- 时长:`1 ~ 30` 秒
- 分辨率:宽高 ∈ `[240, 4096]` px
- 宽高比:`1:8 ~ 8:1`
- 文件大小:≤ 100 MB

#### `reference_voice`（可选,搭配 `reference_image` / `reference_video`)

为参考素材中的主体角色指定参考音色(仅参考音色,与说话内容无关)。

- 格式:`wav` / `mp3`
- 时长:`1 ~ 10` 秒
- 文件大小:≤ 15 MB
- URL 形式同 `url` 字段(http / oss / base64)
- 优先级:
  - `reference_video` 自身有音频但未传 `reference_voice` → 用视频原声
  - 同时传 `reference_video`(含音频)+ `reference_voice` → **`reference_voice` 优先**,覆盖视频原声
- 建议参考音频语种与 prompt 语种保持一致

### `parameters`（可选)

| 参数 | 类型 | 默认 | 取值 | 说明 |
|------|------|------|------|------|
| `resolution` | string | `1080P` | `720P` / `1080P` | **直接影响费用** |
| `ratio` | string | `16:9` | `16:9` / `9:16` / `1:1` / `4:3` / `3:4` | 宽高比,**传 `first_frame` 时自动忽略**,以首帧宽高比为准 |
| `duration` | integer | `5` | 见下方 | **直接影响费用** |
| `prompt_extend` | bool | `true` | `true` / `false` | 智能改写,短 prompt 提升显著,会增加耗时 |
| `watermark` | bool | `false` | `true` / `false` | 右下角水印,文案固定 "AI 生成" |
| `seed` | integer | 随机 | `[0, 2147483647]` | 固定种子提升可复现性 |

#### `duration` 取值范围

- 参考素材**包含视频**(`reference_video`):`[2, 10]` 整数(秒)
- 参考素材**不包含视频**:`[2, 15]` 整数(秒)
- 默认值:`5`

## 步骤 2:轮询任务结果

```
GET /api/v1/tasks/{task_id}
```

### 成功响应示例

```json
{
  "request_id": "52cade0d-...",
  "output": {
    "task_id": "18814247-...",
    "task_status": "SUCCEEDED",
    "submit_time":    "2026-04-02 22:53:19.537",
    "scheduled_time": "2026-04-02 22:53:30.427",
    "end_time":       "2026-04-02 23:00:39.287",
    "orig_prompt": "...",
    "video_url": "https://dashscope-a717.oss-accelerate.aliyuncs.com/xxx.mp4?xxxx"
  },
  "usage": {
    "duration": 15,
    "input_video_duration": 5,
    "output_video_duration": 10,
    "video_count": 1,
    "SR": 720,
    "ratio": "16:9"
  }
}
```

### `usage` 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `duration` | int | **总视频时长(秒),用于计费**(= `input_video_duration` + `output_video_duration`) |
| `input_video_duration` | int | 输入视频总时长(`reference_video` 视频长度) |
| `output_video_duration` | int | 输出视频时长 |
| `video_count` | int | 固定为 1 |
| `SR` | int | 输出分辨率档位(如 720) |
| `ratio` | string | 输出宽高比 |

> 注意:`duration` 包含输入视频时长。如果传了 5 秒的 `reference_video` 并要求生成 10 秒输出,**计费 duration = 15 秒**。

## ArcReel 集成要点

- **R2V 单镜头参考素材上限 = 5**(`max_reference_images: 5`,含 `reference_image + reference_video`)
- **resolutions**:`["720P", "1080P"]`
- **supported_durations**:
  - 含 `reference_video`:`[2, 3, ..., 10]`
  - 不含 `reference_video`:`[2, 3, ..., 15]`
  - ArcReel 默认按不含视频上限取 `[2, ..., 15]`
- **capabilities**:`["reference_images", "first_frame", "generate_audio"]`(音频恒开)
- **水印**:`watermark=false` 是默认值,无需特别处理
- **prompt_extend**:默认 `true`,会增加耗时;ArcReel 是否关闭由项目决定 — 建议**项目级开关**而非硬编 false
- **计费 duration**:含输入视频时长,前端预估时需要把参考视频时长加进去

## 与 wan2.6 差异

| 维度 | wan2.6 | wan2.7 |
|------|--------|--------|
| 参考引用写法 | `character1` / `character2` | `图1` / `图2` 或 `Image 1` / `Image 2`(图像);`视频1` / `Video 1`(视频) |
| 多镜头控制 | 设置 `shot_type=multi` | 在 `prompt` 中直接描述分镜脚本(不支持 `shot_type`) |
| 参考素材入参 | `reference_urls`(字符串数组) | `media`(对象数组,含 `type` / `url`) |
| 分辨率参数 | `size`(如 `1280*720`) | `resolution`(如 `720P`) |
| 音频开关 | 显式 `audio` 参数 | **默认有声**,无需设置 |
| SDK 最低版本 | — | Python `1.25.16+` / Java `2.22.14+` |

ArcReel 集成只规划 wan2.7 系列,不接 wan2.6。

## 同系列模型(待官方扩充)

按 PRD 规划:
- `wan2.7-t2v`(文生视频)
- `wan2.7-i2v`(图生视频,支持首尾帧 + 续写)
- `wan2.7-image`(图像)、`wan2.7-image-pro`

这些模型的 endpoint 路径相同(`/video-generation/video-synthesis` 或图像端点),字段大体同构。实现时按官方文档为准。
