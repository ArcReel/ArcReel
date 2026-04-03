# Seedance 视频生成模型特性与 Python 开发指南

Seedance 模型具备出色的语义理解能力，可根据用户输入的文本、图片、视频、音频等多模态内容，快速生成优质的视频片段。本文为您介绍视频生成模型的通用基础能力，并指导您使用 Python 调用 Video Generation API 生成视频。

## 1. 模型能力概览

本表格展示所有 Seedance 模型支持的能力，方便您对比和选型。

| **能力项**              | **Seedance 2.0**             | **Seedance 2.0 fast**             | **Seedance 1.5 pro**             | **Seedance 1.0 pro**             | **Seedance 1.0 pro fast**             | **Seedance 1.0 lite i2v**             | **Seedance 1.0 lite t2v**             |
| ----------------------- | ---------------------------- | --------------------------------- | -------------------------------- | -------------------------------- | ------------------------------------- | ------------------------------------- | ------------------------------------- |
| **Model ID**            | `doubao-seedance-2-0-260128` | `doubao-seedance-2-0-fast-260128` | `doubao-seedance-1-5-pro-251215` | `doubao-seedance-1-0-pro-250528` | `doubao-seedance-1-0-pro-fast-251015` | `doubao-seedance-1-0-lite-i2v-250428` | `doubao-seedance-1-0-lite-t2v-250428` |
| **文生视频**            | ✅                           | ✅                                | ✅                               | ✅                               | ✅                                    | ✅                                    | ✅                                    |
| **图生视频-首帧**       | ✅                           | ✅                                | ✅                               | ✅                               | ✅                                    | ✅                                    | -                                     |
| **图生视频-首尾帧**     | ✅                           | ✅                                | ✅                               | ✅                               | -                                     | ✅                                    | -                                     |
| **多模态参考(图/视频)** | ✅                           | ✅                                | -                                | -                                | -                                     | ✅ (仅图片)                           | -                                     |
| **编辑/延长视频**       | ✅                           | ✅                                | -                                | -                                | -                                     | -                                     | -                                     |
| **生成有声视频**        | ✅                           | ✅                                | ✅                               | -                                | -                                     | -                                     | -                                     |
| **联网搜索增强**        | ✅                           | ✅                                | -                                | -                                | -                                     | -                                     | -                                     |
| **样片模式(Draft)**     | -                            | -                                 | ✅                               | -                                | -                                     | -                                     | -                                     |
| **返回视频尾帧**        | ✅                           | ✅                                | ✅                               | ✅                               | ✅                                    | ✅                                    | ✅                                    |
| **输出分辨率**          | 480p, 720p                   | 480p, 720p                        | 480p, 720p, 1080p                | 480p, 720p, 1080p                | 480p, 720p, 1080p                     | 480p, 720p, 1080p                     | 480p, 720p, 1080p                     |
| **输出时长(秒)**        | 4~15                         | 4~15                              | 4~12                             | 2~12                             | 2~12                                  | 2~12                                  | 2~12                                  |
| **在线推理 RPM**        | 600                          | 600                               | 600                              | 600                              | 600                                   | 300                                   | 300                                   |
| **并发数**              | 10                           | 10                                | 10                               | 10                               | 10                                    | 5                                     | 5                                     |
| **离线推理(Flex)**      | -                            | -                                 | ✅ (5000亿 TPD)                  | ✅ (5000亿 TPD)                  | ✅ (5000亿 TPD)                       | ✅ (2500亿 TPD)                       | ✅ (2500亿 TPD)                       |

_(注：✅ 表示支持，- 表示不支持或功能未开放)_

## 2. 新手入门流程

> **提示**：调用 API 前，请确保已安装 Python SDK：`pip install 'volcengine-python-sdk[ark]'`，并配置好环境变量 `ARK_API_KEY`。

视频生成是一个**异步过程**：

1. 成功调用创建接口后，API 返回任务 ID (`task_id`)。
2. 轮询查询接口，直到任务状态变为 `succeeded`（或使用 Webhook 接收通知）。
3. 任务完成后，提取 `content.video_url` 下载 MP4 文件。

### 步骤 1: 创建视频生成任务

```
import os
from volcenginesdkarkruntime import Ark

client = Ark(api_key=os.environ.get("ARK_API_KEY"))

if __name__ == "__main__":
    resp = client.content_generation.tasks.create(
        model="doubao-seedance-2-0-260128",
        content=[
            {
                "type": "text",
                "text": "女孩抱着狐狸，女孩睁开眼，温柔地看向镜头，狐狸友善地抱着，镜头缓缓拉出，女孩的头发被风吹动，可以听到风声"
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": "[https://ark-project.tos-cn-beijing.volces.com/doc_image/i2v_foxrgirl.png](https://ark-project.tos-cn-beijing.volces.com/doc_image/i2v_foxrgirl.png)"
                }
            }
        ],
        generate_audio=True,
        ratio="adaptive",
        duration=5,
        watermark=False,
    )
    print(f"Task Created: {resp.id}")
```

### 步骤 2: 查询任务状态

```
import os
from volcenginesdkarkruntime import Ark

client = Ark(api_key=os.environ.get("ARK_API_KEY"))

if __name__ == "__main__":
    # 替换为您创建任务时返回的 ID
    resp = client.content_generation.tasks.get(task_id="cgt-2025****")
    print(resp)

    if resp.status == "succeeded":
        print(f"Video URL: {resp.content.video_url}")
```

## 3. 场景开发实战 (Python)

### 3.1 纯文本生成视频 (Text-to-Video)

根据用户输入的提示词生成视频，结果具有较大的随机性，可用于激发创作灵感。

```
import os
import time
from volcenginesdkarkruntime import Ark

client = Ark(api_key=os.environ.get("ARK_API_KEY"))

create_result = client.content_generation.tasks.create(
    model="doubao-seedance-2-0-260128",
    content=[
        {
            "type": "text",
            "text": "写实风格，晴朗的蓝天之下，一大片白色的雏菊花田，镜头逐渐拉近，最终定格在一朵雏菊花的特写上，花瓣上有几颗晶莹的露珠"
        }
    ],
    ratio="16:9",
    duration=5,
    watermark=True,
)

# 轮询获取结果
task_id = create_result.id
while True:
    get_result = client.content_generation.tasks.get(task_id=task_id)
    if get_result.status == "succeeded":
        print(f"任务成功! 视频下载地址: {get_result.content.video_url}")
        break
    elif get_result.status == "failed":
        print(f"任务失败: {get_result.error}")
        break
    else:
        print(f"处理中 ({get_result.status})... 等待 10 秒")
        time.sleep(10)
```

### 3.2 图生视频 - 基于首帧 (Image-to-Video)

指定视频的首帧图片，模型基于该图片生成连贯视频。设置 `generate_audio=True` 可同步生成音频。

```
# 构建 content 列表
content = [
    {
        "type": "text",
        "text": "女孩抱着狐狸，镜头缓缓拉出，头发被风吹动，可以听到风声"
    },
    {
        "type": "image_url",
        "image_url": {
            "url": "[https://ark-project.tos-cn-beijing.volces.com/doc_image/i2v_foxrgirl.png](https://ark-project.tos-cn-beijing.volces.com/doc_image/i2v_foxrgirl.png)"
        }
    }
]

create_result = client.content_generation.tasks.create(
    model="doubao-seedance-2-0-260128",
    content=content,
    generate_audio=True, # 开启音频生成
    ratio="adaptive",
    duration=5,
    watermark=True,
)
```

### 3.3 图生视频 - 基于首尾帧

通过指定视频的起始和结束图片，生成流畅衔接首、尾帧的视频。

```
content = [
    {
        "type": "text",
        "text": "图中女孩对着镜头说'茄子'，360度环绕运镜"
    },
    {
        "type": "image_url",
        "image_url": {
            "url": "[https://ark-project.tos-cn-beijing.volces.com/doc_image/seepro_first_frame.jpeg](https://ark-project.tos-cn-beijing.volces.com/doc_image/seepro_first_frame.jpeg)"
        },
        "role": "first_frame" # 指定角色为首帧
    },
    {
        "type": "image_url",
        "image_url": {
            "url": "[https://ark-project.tos-cn-beijing.volces.com/doc_image/seepro_last_frame.jpeg](https://ark-project.tos-cn-beijing.volces.com/doc_image/seepro_last_frame.jpeg)"
        },
        "role": "last_frame"  # 指定角色为尾帧
    }
]

create_result = client.content_generation.tasks.create(
    model="doubao-seedance-2-0-260128",
    content=content,
    ratio="adaptive",
    duration=5
)
```

### 3.4 图生视频 - 基于参考图

模型能精准提取参考图片（支持输入 1-4 张）中各类对象的关键特征，并依据这些特征在视频生成过程中高度还原对象的形态、色彩和纹理等细节，确保生成的视频与参考图的视觉风格一致。

```
content = [
    {
        "type": "text",
        "text": "[图1]戴着眼镜穿着蓝色T恤的男生和[图2]的柯基小狗，坐在[图3]的草坪上，视频卡通风格"
    },
    {
        "type": "image_url",
        "image_url": {
            "url": "[https://ark-project.tos-cn-beijing.volces.com/doc_image/seelite_ref_1.png](https://ark-project.tos-cn-beijing.volces.com/doc_image/seelite_ref_1.png)"
        },
        "role": "reference_image" # 指定为参考图
    },
    {
        "type": "image_url",
        "image_url": {
            "url": "[https://ark-project.tos-cn-beijing.volces.com/doc_image/seelite_ref_2.png](https://ark-project.tos-cn-beijing.volces.com/doc_image/seelite_ref_2.png)"
        },
        "role": "reference_image"
    },
    {
        "type": "image_url",
        "image_url": {
            "url": "[https://ark-project.tos-cn-beijing.volces.com/doc_image/seelite_ref_3.png](https://ark-project.tos-cn-beijing.volces.com/doc_image/seelite_ref_3.png)"
        },
        "role": "reference_image"
    }
]

create_result = client.content_generation.tasks.create(
    # 注意：需选择支持该功能的模型，例如 Seedance 1.0 lite i2v
    model="doubao-seedance-1-0-lite-i2v-250428",
    content=content,
    ratio="16:9",
    duration=5
)
```

### 3.5 视频任务管理

**查询任务列表：**

```
resp = client.content_generation.tasks.list(
    page_size=3,
    status="succeeded",
)
print(resp)
```

**删除或取消任务：**

```
client.content_generation.tasks.delete(task_id="cgt-2025****")
```

## 4. 提示词建议

为了获得更优质、更符合预期的生成结果，推荐遵循以下提示词编写原则：

- **核心公式：提示词 = 主体 + 运动 + 背景 + 运动 + 镜头 + 运动 ...** \* **直白准确**：用简洁准确的自然语言写出你想要的效果，将抽象描述换成具象描述。
- **分步走策略**：如果有较为明确的效果预期，建议先用生图模型生成符合预期的图片，再用**图生视频**进行视频片段的生成。
- **主次分明**：注意删除不重要的部分，将重要内容前置。
- **利用随机性**：纯文生视频会有较大的结果随机性，非常适合用于激发创作灵感。
- **输入质量**：图生视频时请尽量上传高清高质量的图片，上传图片的质量对生成的最终视频效果影响极大。

## 5. 高级开发特性

### 5.1 输出规格参数 (Request Body 控制)

强校验模式下，建议直接在 Request Body 传入以下参数控制视频规格：

| **参数**       | **说明**   | **支持取值示例**                                        |
| -------------- | ---------- | ------------------------------------------------------- |
| `resolution`   | 输出分辨率 | `480p`, `720p`, `1080p`                                 |
| `ratio`        | 视频宽高比 | `16:9`, `9:16`, `1:1`, `4:3`, `3:4`, `21:9`, `adaptive` |
| `duration`     | 时长(秒)   | 整数类型，例如 `5`                                      |
| `frames`       | 生成帧数   | 优先使用 duration。若用 frames，须满足 `25 + 4n` 格式   |
| `seed`         | 随机种子   | 整数值，用于复现生成效果                                |
| `camera_fixed` | 锁定镜头   | `true` 或 `false`                                       |
| `watermark`    | 是否带水印 | `true` 或 `false`                                       |

### 5.2 离线推理 (Flex Tier)

对于非实时场景，配置 `service_tier="flex"` 可以将调用价格降低 50%。

```
create_result = client.content_generation.tasks.create(
    model="doubao-seedance-1-5-pro-251215",
    content=[...], # 略
    service_tier="flex",             # 开启离线推理
    execution_expires_after=172800,  # 设定任务超时时间
)
```

### 5.3 样片模式 (Draft Mode)

帮助低成本验证 prompt 意图、镜头调度等。（_注：目前仅 Seedance 1.5 pro 支持_）

**第一步：生成低成本样片**

```
create_result = client.content_generation.tasks.create(
    model="doubao-seedance-1-5-pro-251215",
    content=[...],
    seed=20,
    duration=6,
    draft=True # 开启样片模式
)
# 获取返回的 draft_task_id: "cgt-2026****-pzjqb"
```

**第二步：基于样片生成正式视频**

确认样片满意后，利用 draft task id 生成高清完整版：

```
create_result = client.content_generation.tasks.create(
    model="doubao-seedance-1-5-pro-251215",
    content=[
        {
            "type": "draft_task",
            "draft_task": {"id": "cgt-2026****-pzjqb"} # 引用样片任务
        }
    ],
    resolution="720p",
    watermark=False
)
```

### 5.4 Webhook 状态回调通知

通过设置 `callback_url`，可以避免轮询造成的资源浪费。下方是一个接收方舟 Webhook 的简单 Flask 服务示例：

```
from flask import Flask, request, jsonify
import logging

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

@app.route('/webhook/callback', methods=['POST'])
def video_task_callback():
    callback_data = request.get_json()
    if not callback_data:
        return jsonify({"code": 400, "msg": "Invalid data"}), 400

    task_id = callback_data.get('id')
    status = callback_data.get('status')

    logging.info(f"Task Callback | ID: {task_id} | Status: {status}")

    if status == 'succeeded':
        # 此处可以触发业务逻辑，入库或通过API抓取内容
        pass

    return jsonify({"code": 200, "msg": "Success"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
```

## 6. 使用限制与裁剪规则

### 6.1 多模态输入限制

- **图片**: 单张 $<30$ MB。支持 jpeg, png, webp 等。尺寸比在 `(0.4, 2.5)` 之间，长度 `300 ~ 6000` px。
- **视频**: 单个 $<50$ MB。支持 mp4, mov。时长 `2~15` 秒。帧率 `24~60` FPS。
- **音频**: 单个 $<15$ MB。支持 wav, mp3。时长 `2~15` 秒。

### 6.2 自动图片裁剪规则 (Crop Rule)

当您指定的 `ratio` (视频比例) 与实际传入的图片比例不一致时，服务会触发 **居中裁剪** 逻辑：

1. 若原图比目标更 "窄高"（原始宽高比 < 目标宽高比），则 **以宽为准**，上下裁切居中。
2. 若原图比目标更 "宽扁"（原始宽高比 > 目标宽高比），则 **以高为准**，左右裁切居中。

> **建议**：尽量传入与目标 `ratio` 比例接近的高清图片，以获得最佳成片效果，避免关键主体被裁剪。

### 6.3 任务生命周期

任务数据（如状态、视频下载链接）**仅保留 24 小时**，超时将自动清除。请在回调或轮询确认成功后，尽快将产物下载转存至您的 OSS 等存储空间。
