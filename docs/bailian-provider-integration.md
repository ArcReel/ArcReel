# 阿里云百炼供应商接入文档

## 1. 概述

本文档描述如何在 ArcReel 中接入阿里云百炼（DashScope）供应商，支持以下能力：

- **文本生成**：Qwen 3.6 Plus 系列
- **图像生成与编辑**：Wan 2.7 Image 系列
- **视频生成**：Wan 2.7 视频系列（文生视频、图生视频、参考视频生成、视频编辑）

---

## 2. 供应商定位

### 2.1 供应商标识

- **Provider Key**: `bailian`
- **Display Name**: 阿里云百炼
- **Description**: 阿里云百炼（DashScope）提供通义千问文本模型、万相图像生成和视频生成能力，适合国内网络环境和阿里云生态。

### 2.2 认证方式

统一使用 API Key 认证：

```http
Authorization: Bearer $DASHSCOPE_API_KEY
```

### 2.3 配置字段

**必填字段**：

- `api_key`：DashScope API Key

**可选字段**：

- `base_url`：仅文本模型 OpenAI 兼容模式需要（默认：`https://dashscope.aliyuncs.com/compatible-mode/v1`）
- `image_max_workers`：图像生成并发数
- `video_max_workers`：视频生成并发数

**敏感字段**：

- `api_key`

---

## 3. 文本生成（Qwen 3.6 Plus）

### 3.1 模型信息


| 模型名称           | 显示名称          | 能力                                         | 默认  |
| -------------- | ------------- | ------------------------------------------ | --- |
| `qwen3.6-plus` | Qwen 3.6 Plus | text_generation, structured_output, vision | ✓   |
| `qwen-max`     | Qwen Max      | text_generation, structured_output, vision |     |
| `qwen-plus`    | Qwen Plus     | text_generation, structured_output         |     |
| `qwen-turbo`   | Qwen Turbo    | text_generation, structured_output         |     |


### 3.2 API 调用方式

**推荐方式**：OpenAI 兼容模式

**Base URL**：

```
https://dashscope.aliyuncs.com/compatible-mode/v1
```

**优势**：

- 可复用现有 `AsyncOpenAI` 客户端
- 支持 structured output（response_format）
- 支持 vision（图像输入）
- 与项目现有 OpenAI 后端实现模式一致

**请求格式**：

```python
# 使用 AsyncOpenAI 客户端
client = AsyncOpenAI(
    api_key="your-dashscope-api-key",
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
)

response = await client.chat.completions.create(
    model="qwen3.6-plus",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello"}
    ],
    max_tokens=1000
)
```

### 3.3 特性支持

- ✅ 文本生成
- ✅ Structured Output（JSON Schema）
- ✅ Vision（图像理解）
- ✅ 流式输出
- ✅ max_output_tokens
- ⚠️ 需要 Instructor 降级方案（部分 schema 可能不兼容）

### 3.4 实现建议

参考 `lib/text_backends/openai.py`，主要修改：

- Provider 常量改为 `PROVIDER_BAILIAN`
- 默认模型改为 `qwen3.6-plus`
- 默认 `base_url` 指向 DashScope 兼容端点
- 保留 schema 错误降级逻辑

---

## 4. 图像生成（Wan 2.7 Image）

### 4.1 模型信息


| 模型名称               | 显示名称              | 能力                            | 默认  |
| ------------------ | ----------------- | ----------------------------- | --- |
| `wan2.7-image-pro` | Wan 2.7 Image Pro | text_to_image, image_to_image | ✓   |
| `wan2.7-image`     | Wan 2.7 Image     | text_to_image, image_to_image |     |


### 4.2 API Endpoint

**同步调用**：

```
POST https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation
```

**异步调用**（推荐）：

```
POST https://dashscope.aliyuncs.com/api/v1/services/aigc/image-generation/generation
```

（需添加 Header：`X-DashScope-Async: enable`）

**查询结果**：

```
GET https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}
```

### 4.3 请求参数

```json
{
    "model": "wan2.7-image-pro",
    "input": {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"text": "一间有着精致窗户的花店，漂亮的木质门，摆放着花朵"}
                ]
            }
        ]
    },
    "parameters": {
        "size": "2K",
        "n": 1,
        "watermark": false,
        "thinking_mode": true
    }
}
```

### 4.4 返回参数

``` ts
// 同步调用
{
    "output": {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": [
                        {
                            "image": "https://dashscope-xxx.oss-xxx.aliyuncs.com/xxx.png?Expires=xxx",
                            "type": "image"
                        }
                    ],
                    "role": "assistant"
                }
            }
        ],
        "finished": true
    },
    "usage": {
        "image_count": 1,
        "input_tokens": 10867,
        "output_tokens": 2,
        "size": "1488*704",
        "total_tokens": 10869
    },
    "request_id": "71dfc3c6-f796-9972-97e4-bc4efc4faxxx"
}
// 异步调用
{
    "output": {
        "task_status": "PENDING",
        "task_id": "0385dc79-5ff8-4d82-bcb6-xxxxxx"
    },
    "request_id": "4909100c-7b5a-9f92-bfe5-xxxxxx"
}
```

### 4.5 分辨率映射

ArcReel → DashScope：


| aspect_ratio | size 参数     |
| ------------ | ----------- |
| `9:16`       | `720*1280`  |
| `16:9`       | `1280*720`  |
| `1:1`        | `1024*1024` |
| `3:4`        | `768*1152`  |
| `4:3`        | `1152*768`  |


### 4.5 功能支持

- ✅ 文生图（text_to_image）
- ✅ 图像编辑（image_to_image）
- ✅ 参考图输入（最多 3 张）
- ✅ Negative Prompt
- ✅ Seed 控制
- ✅ 水印控制

### 4.6 实现建议

- 使用异步任务模式（提交任务 → 轮询状态 → 下载图片）
- 参考图通过 `messages` 中的 `image` 字段传递
- 图片 URL 有效期 24 小时，需及时下载到本地
- 支持多图输入与多图输出

---

## 5. 视频生成（Wan 2.7 Video）

### 5.1 模型信息


| 模型名称               | 显示名称               | 能力                                 | 默认  |
| ------------------ | ------------------ | ---------------------------------- | --- |
| `wan2.7-t2v`       | Wan 2.7 T2V        | text_to_video, generate_audio      | ✓   |
| `wan2.7-i2v`       | Wan 2.7 I2V        | image_to_video, generate_audio     |     |
| `wan2.7-r2v`       | Wan 2.7 R2V        | reference_to_video, generate_audio |     |
| `wan2.7-videoedit` | Wan 2.7 Video Edit | video_editing                      |     |


### 5.2 API Endpoint

**提交任务**：

```
POST https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis
```

**查询结果**：

```
GET https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}
```

### 5.3 文生视频（T2V）

**模型**：`wan2.7-t2v`

**请求参数**：

```json
{
  "model": "wan2.7-t2v",
  "input": {
    "prompt": "一只猫在草地上奔跑"
  },
  "parameters": {
        "resolution": "1080P",
        "ratio": "16:9",
        "prompt_extend": true,
        "watermark": true,
        "duration": 15
    }
}
```

**支持的分辨率**：

- 480P、720P、1080P

**支持的时长**：

- 2-15 秒

**特性**：

- ✅ 文本生成视频
- ✅ 音频生成
- ✅ 单镜头/多镜头
- ✅ Prompt 扩展

### 5.4 图生视频（I2V）

**模型**：`wan2.7-i2v`

**请求参数**：

```json
{
    "model": "wan2.7-i2v",
    "input": {
        "prompt": "一幅都市奇幻艺术的场景。一个充满动感的涂鸦艺术角色。一个由喷漆所画成的少年，正从一面混凝土墙上活过来。他一边用极快的语速演唱一首英文rap，一边摆着一个经典的、充满活力的说唱歌手姿势。场景设定在夜晚一个充满都市感的铁路桥下。灯光来自一盏孤零零的街灯，营造出电影般的氛围，充满高能量和惊人的细节。视频的音频部分完全由rap构成，没有其他对话或杂音。",
        "media": [
            {
                "type": "first_frame",
                "url": "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250925/wpimhv/rap.png"
            },
            {
                "type": "driving_audio",
                "url": "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250925/ozwpvi/rap.mp3"
                
            }
        ]
    },
    "parameters": {
        "resolution": "720P",
        "duration": 10,
        "prompt_extend": true,
        "watermark": true
    }
}
```

**特性**：

- ✅ 首帧图像输入
- ✅ 音频驱动（可选）
- ✅ 单镜头/多镜头
- ✅ 2-15 秒时长

### 5.5 参考视频生成（R2V）

**模型**：`wan2.7-r2v`

**请求参数**：

```json
{
    "model": "wan2.7-r2v",
    "input": {
        "prompt": "视频2抱着图3在咖啡厅里弹奏一支舒缓的美式乡村民谣，视频1笑着看着视频2",
        "media": [
            {
                "type": "reference_video",
                "url": "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260129/hfugmr/wan-r2v-role1.mp4"
            },
            {
                "type": "reference_video",
                "url": "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260129/qigswt/wan-r2v-role2.mp4"
            },
            {
                "type": "reference_image",
                "url": "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260129/qpzxps/wan-r2v-object4.png"
            }
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

**特性**：

- ✅ 多参考输入（最多 5 个：0-5 张图片，0-3 个视频）
- ✅ 角色一致性保持
- ✅ 声音参考
- ✅ 多镜头智能分镜

### 5.6 视频编辑（VideoEdit）

**模型**：`wan2.7-videoedit`

**请求参数**：

```json
{
    "model": "wan2.7-videoedit",
    "input": {
        "prompt": "将整个画面转换为黏土风格",
        "media": [
            {
                "type": "video",
                "url": "https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20260402/ldnfdf/wan2.7-videoedit-style-change.mp4"
            }
        ]
    },
    "parameters": {
        "resolution": "720P",
        "prompt_extend": true,
        "watermark": true
    }
}
```

**特性**：

- ✅ 视频风格迁移
- ✅ 内容替换
- ✅ 音频处理（保留或重新生成）
- ✅ 多模态输入（文本+图片+视频）

### 5.7 分辨率与时长约束


| 模型               | 分辨率             | 时长范围  |
| ---------------- | --------------- | ----- |
| wan2.7-t2v       | 480P/720P/1080P | 2-15s |
| wan2.7-i2v       | 480P/720P/1080P | 2-15s |
| wan2.7-r2v       | 480P/720P/1080P | 2-10s |
| wan2.7-videoedit | 720P/1080P      | 2-10s |


### 5.8 实现建议

- 使用异步任务模式（提交 → 轮询 → 下载）
- 视频 URL 有效期 24 小时
- 支持 `shot_type`：`single`（单镜头）或 `multi`（多镜头）
- 音频生成可选（`audio: true/false`）
- 建议添加水印控制（`watermark: true/false`）

## tips: 上传文件获取临时URL

**说明**： 上传文件获取临时URL，用于上传文件到OSS，并获取临时URL。

### 步骤 1：获取临时URL

```python
import os
import requests
from pathlib import Path
from datetime import datetime, timedelta

def get_upload_policy(api_key, model_name):
    """获取文件上传凭证"""
    url = "https://dashscope.aliyuncs.com/api/v1/uploads"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    params = {
        "action": "getPolicy",
        "model": model_name
    }
    
    response = requests.get(url, headers=headers, params=params)
    if response.status_code != 200:
        raise Exception(f"Failed to get upload policy: {response.text}")
    
    return response.json()['data']

def upload_file_to_oss(policy_data, file_path):
    """将文件上传到临时存储OSS"""
    file_name = Path(file_path).name
    key = f"{policy_data['upload_dir']}/{file_name}"
    
    with open(file_path, 'rb') as file:
        files = {
            'OSSAccessKeyId': (None, policy_data['oss_access_key_id']),
            'Signature': (None, policy_data['signature']),
            'policy': (None, policy_data['policy']),
            'x-oss-object-acl': (None, policy_data['x_oss_object_acl']),
            'x-oss-forbid-overwrite': (None, policy_data['x_oss_forbid_overwrite']),
            'key': (None, key),
            'success_action_status': (None, '200'),
            'file': (file_name, file)
        }
        
        response = requests.post(policy_data['upload_host'], files=files)
        if response.status_code != 200:
            raise Exception(f"Failed to upload file: {response.text}")
    
    return f"oss://{key}"

def upload_file_and_get_url(api_key, model_name, file_path):
    """上传文件并获取URL"""
    # 1. 获取上传凭证，上传凭证接口有限流，超出限流将导致请求失败
    policy_data = get_upload_policy(api_key, model_name) 
    # 2. 上传文件到OSS
    oss_url = upload_file_to_oss(policy_data, file_path)
    
    return oss_url

# 使用示例
if __name__ == "__main__":
    # 从环境变量中获取API Key 或者 在代码中设置 api_key = "your_api_key"
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise Exception("请设置DASHSCOPE_API_KEY环境变量")
        
    # 设置model名称
    model_name="qwen-vl-plus"

    # 待上传的文件路径
    file_path = "/tmp/cat.png"  # 替换为实际文件路径
    
    try:
        public_url = upload_file_and_get_url(api_key, model_name, file_path)
        expire_time = datetime.now() + timedelta(hours=48)
        print(f"文件上传成功，有效期为48小时，过期时间: {expire_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"临时URL: {public_url}")
        print("注意：使用oss://形式的临时URL时，必须在HTTP请求头（Header）中显式添加参数：X-DashScope-OssResourceResolve: enable，具体请参考：https://help.aliyun.com/zh/model-studio/get-temporary-file-url#http-call")

    except Exception as e:
        print(f"Error: {str(e)}")
```

### 步骤 2：使用临时URL调用模型

请求模型时，相关资源 替换成 `oss://` 协议, 例如： `oss://dashscope-instant/xxx/2024-07-18/xxxx/cat.png`
---

## 6. 供应商注册配置

### 6.1 registry.py 配置

```python
"bailian": ProviderMeta(
    display_name="阿里云百炼",
    description="阿里云百炼（DashScope）提供通义千问文本模型、万相图像生成和视频生成能力。",
    required_keys=["api_key"],
    optional_keys=["base_url", "image_max_workers", "video_max_workers"],
    secret_keys=["api_key"],
    models={
        # --- text ---
        "qwen3.6-plus": ModelInfo(
            display_name="Qwen 3.6 Plus",
            media_type="text",
            capabilities=["text_generation", "structured_output", "vision"],
            default=True,
        ),
        "qwen-max": ModelInfo(
            display_name="Qwen Max",
            media_type="text",
            capabilities=["text_generation", "structured_output", "vision"],
        ),
        "qwen-plus": ModelInfo(
            display_name="Qwen Plus",
            media_type="text",
            capabilities=["text_generation", "structured_output"],
        ),
        "qwen-turbo": ModelInfo(
            display_name="Qwen Turbo",
            media_type="text",
            capabilities=["text_generation", "structured_output"],
        ),
        # --- image ---
        "wan2.7-image-pro": ModelInfo(
            display_name="Wan 2.7 Image Pro",
            media_type="image",
            capabilities=["text_to_image", "image_to_image"],
            default=True,
        ),
        "wan2.7-image": ModelInfo(
            display_name="Wan 2.7 Image",
            media_type="image",
            capabilities=["text_to_image", "image_to_image"],
        ),
        # --- video ---
        "wan2.7-t2v": ModelInfo(
            display_name="Wan 2.7 T2V",
            media_type="video",
            capabilities=["text_to_video", "generate_audio"],
            default=True,
            supported_durations=list(range(2, 16)),
        ),
        "wan2.7-i2v": ModelInfo(
            display_name="Wan 2.7 I2V",
            media_type="video",
            capabilities=["image_to_video", "generate_audio"],
            supported_durations=list(range(2, 16)),
        ),
        "wan2.7-r2v": ModelInfo(
            display_name="Wan 2.7 R2V",
            media_type="video",
            capabilities=["reference_to_video", "generate_audio"],
            supported_durations=list(range(2, 11)),
        ),
        "wan2.7-videoedit": ModelInfo(
            display_name="Wan 2.7 Video Edit",
            media_type="video",
            capabilities=["video_editing"],
            supported_durations=list(range(2, 11)),
        ),
    },
)
```

### 6.2 providers.py 常量

```python
PROVIDER_BAILIAN = "bailian"
```

---

## 7. 国际化（i18n）

### 7.1 中文（lib/i18n/zh/providers.py）

```python
"bailian": {
    "name": "阿里云百炼",
    "description": "阿里云百炼（DashScope）提供通义千问文本模型、万相图像生成和视频生成能力。",
}
```

### 7.2 英文（lib/i18n/en/providers.py）

```python
"bailian": {
    "name": "Alibaba Cloud Bailian",
    "description": "Alibaba Cloud Bailian (DashScope) provides Qwen text models, Wan image generation, and video generation capabilities.",
}
```

---

## 8. 实现优先级

### Phase 1（核心功能）

1. ✅ `lib/providers.py` 添加 `PROVIDER_BAILIAN` 常量
2. ✅ `lib/config/registry.py` 注册供应商元数据
3. ✅ `lib/text_backends/bailian.py` 实现文本生成（基于 OpenAI 兼容）
4. ✅ `lib/image_backends/bailian.py` 实现图像生成（异步任务）
5. ✅ `lib/video_backends/bailian.py` 实现视频生成（T2V + I2V）
6. ✅ i18n 翻译

### Phase 2（扩展功能）

1. ⏳ 视频参考生成（R2V）
2. ⏳ 视频编辑（VideoEdit）
3. ⏳ 更多 Qwen 模型变体
4. ⏳ 图像高级参数（风格、强度等）

---

## 9. 测试与验证

### 9.1 Lint & Format

```bash
uv run ruff check lib/providers.py lib/config/registry.py lib/text_backends/bailian.py lib/image_backends/bailian.py lib/video_backends/bailian.py
uv run ruff format lib/providers.py lib/config/registry.py lib/text_backends/bailian.py lib/image_backends/bailian.py lib/video_backends/bailian.py
```

### 9.2 功能测试

- 供应商注册可加载
- 文本后端可实例化并生成文本
- 图像后端可提交任务并轮询结果
- 视频后端可提交任务并轮询结果
- 异步任务状态处理正确（PENDING/RUNNING/SUCCEEDED/FAILED）
- 错误处理和重试机制正常

---

## 10. 参考资料

- [阿里云百炼新版智能体应用API](https://help.aliyun.com/zh/model-studio/new-agent-application-api-reference)
- [通义千问API参考](https://help.aliyun.com/zh/model-studio/developer-reference/use-qwen-by-calling-api)
- [万相图像编辑模型](https://help.aliyun.com/zh/model-studio/wan-image-edit)
- [万相图生视频API](https://www.alibabacloud.com/help/en/model-studio/image-to-video-api-reference/)
- [万相参考视频生成API](http://www.alibabacloud.com/help/en/model-studio/wan-video-to-video-api-reference)
- [万相视频编辑API](https://www.alibabacloud.com/help/en/model-studio/wan-video-editing-api-reference)
- [Qwen 3.6 Plus API Guide](https://apidog.com/blog/qwen3-6-plus-api/)
- [Wan 2.7 Features & API Access](https://wavespeed.ai/blog/posts/wan-2-7-features-api-upgrade/)

---

## 11. 注意事项

1. **API Key 区域性**：DashScope API Key 有区域限制，需使用对应区域的 endpoint
2. **异步任务有效期**：任务结果 URL 有效期 24 小时，需及时下载
3. **模型名称**：部分模型名称包含斜杠（如 `wan2.7-t2v`），需正确处理
4. **OpenAI 兼容性**：Qwen 模型的 OpenAI 兼容模式可能对某些 schema 不完全支持，需保留降级方案
5. **视频时长约束**：不同模型支持的时长范围不同，需在请求前验证
6. **分辨率格式**：图像使用 `"2K"` 格式，视频使用 `"1280*720"` 格式
7. **并发控制**：建议配置 `image_max_workers` 和 `video_max_workers` 避免超出配额

---

**文档版本**：v1.0  
**最后更新**：2026-04-15