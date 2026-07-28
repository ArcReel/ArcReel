# DashScope API 概览

## Base URL（按地域）

阿里百炼按地域隔离,**模型、Endpoint URL、API Key 必须在同一地域**,跨地域调用直接失败。

| 地域 | Base URL |
|------|----------|
| 华北 2(北京) | `https://dashscope.aliyuncs.com/api/v1` |
| 新加坡 | `https://dashscope-intl.aliyuncs.com/api/v1` |
| 美国(弗吉尼亚) | `https://dashscope-us.aliyuncs.com/api/v1` |
| 德国(法兰克福) | `https://{WorkspaceId}.eu-central-1.maas.aliyuncs.com/api/v1` |

ArcReel 集成默认以**北京**为起点,后续按需扩充。

## OpenAI 兼容模式 Base URL（文本)

文本走 OpenAI 兼容协议:

```
https://dashscope.aliyuncs.com/compatible-mode/v1
```

派生规则:
- host 段(`https://dashscope.aliyuncs.com`)与原生 base 一致
- 后缀 `/compatible-mode/v1` 是文本路径,与原生 `/api/v1` 区分

## 鉴权

所有接口统一 Bearer:

```
Authorization: Bearer $DASHSCOPE_API_KEY
```

API Key 与地域绑定:北京/新加坡/美国 各自有独立 Key,无法互换。

## 异步任务调用模式（图像/视频)

图像与视频生成耗时较长,**强制异步**。同步调用会报错:
> current user api does not support synchronous calls

### 必带请求头

| Header | 必选 | 值 |
|--------|------|-----|
| `Content-Type` | 是 | `application/json` |
| `Authorization` | 是 | `Bearer sk-xxxx` |
| `X-DashScope-Async` | 是 | `enable`（**缺失即报错**） |

### 调用流程

```
┌─ 步骤 1:创建任务 ─────────────────────────────┐
│ POST /api/v1/services/aigc/video-generation/  │
│        video-synthesis                         │
│ → 返回 output.task_id + task_status=PENDING    │
└────────────────────┬───────────────────────────┘
                     │
                     ▼
┌─ 步骤 2:轮询任务结果 ─────────────────────────┐
│ GET /api/v1/tasks/{task_id}                   │
│ → 状态机:PENDING → RUNNING → SUCCEEDED/FAILED │
│ → SUCCEEDED 时返回 output.video_url           │
└────────────────────────────────────────────────┘
```

### 任务状态枚举

| 状态 | 含义 |
|------|------|
| `PENDING` | 任务排队中 |
| `RUNNING` | 任务处理中 |
| `SUCCEEDED` | 任务执行成功(响应含 `output.video_url`) |
| `FAILED` | 任务执行失败(响应含 `output.code` / `output.message`) |
| `CANCELED` | 任务已取消 |
| `UNKNOWN` | 任务不存在或状态未知(常见原因:`task_id` 超过 24h 有效期) |

### 提交响应示例

成功:
```json
{
  "output": {
    "task_status": "PENDING",
    "task_id": "0385dc79-5ff8-4d82-bcb6-xxxxxx"
  },
  "request_id": "4909100c-7b5a-9f92-bfe5-xxxxxx"
}
```

失败:
```json
{
  "code": "InvalidApiKey",
  "message": "No API-key provided.",
  "request_id": "7438d53d-6eb8-4596-8835-xxxxxx"
}
```

### 轮询响应示例（SUCCEEDED）

```json
{
  "request_id": "35137489-2862-96cb-b6f2-xxxxxx",
  "output": {
    "task_id": "1469cfc3-3004-4d9e-ab10-xxxxxx",
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

## 关键约束

- **task_id 有效期**:24 小时,过期后查询返回 `task_status=UNKNOWN`
- **video_url 有效期**:24 小时,需要尽快下载并转存(OSS、本地)
- **视频格式**:MP4 H.264 编码
- **轮询间隔建议**:15 秒
- **轮询 RPS 上限**:默认 20,需要更高频请用异步回调
- **请勿重复创建任务**:`task_id` 已返回后只需轮询,重复提交会重复计费

## 错误码

参见 [阿里百炼错误码文档](https://help.aliyun.com/zh/model-studio/error-code)。
