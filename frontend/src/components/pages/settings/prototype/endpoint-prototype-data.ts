// PROTOTYPE — wayfinder #2129 自定义调用端点设置页 UI 原型的共享 mock 数据。
// 全部只读假数据，不发任何请求；文案硬编码中文，不入 i18n。评审结束整目录删除。

export interface MockEndpoint {
  key: string;
  name: string;
  kind: "python" | "declarative" | "custom";
  author: string;
  version: string;
  mediaType: "video";
  path: string;
  refCount: number; // 被模型行引用数（409 拒删口径）
  updatedAt: string;
}

export const MOCK_ENDPOINTS: MockEndpoint[] = [
  // 内置 · 随版声明式（#2146 首批收编）
  { key: "newapi", name: "New API 视频任务", kind: "declarative", author: "arcreel", version: "1.3.0", mediaType: "video", path: "/v1/video/generations", refCount: 3, updatedAt: "随版" },
  { key: "v2_video_generations", name: "V2 Video Generations", kind: "declarative", author: "arcreel", version: "1.1.0", mediaType: "video", path: "/v2/videos/generations", refCount: 1, updatedAt: "随版" },
  { key: "minimax-hailuo-v1", name: "MiniMax Hailuo v1", kind: "declarative", author: "arcreel", version: "1.0.0", mediaType: "video", path: "/v1/video_generation", refCount: 0, updatedAt: "随版" },
  { key: "minimax-s2v-01", name: "MiniMax S2V-01", kind: "declarative", author: "arcreel", version: "1.0.0", mediaType: "video", path: "/v1/video_generation", refCount: 0, updatedAt: "随版" },
  // 内置 · Python
  { key: "kling", name: "Kling 视频生成", kind: "python", author: "arcreel", version: "—", mediaType: "video", path: "/v1/videos/text2video", refCount: 2, updatedAt: "随版" },
  { key: "vidu", name: "Vidu 视频生成", kind: "python", author: "arcreel", version: "—", mediaType: "video", path: "/ent/v2/text2video", refCount: 0, updatedAt: "随版" },
  // 我的端点（ce- 前缀，系统分配）
  { key: "ce-7f3a2c", name: "偶得视频 · 提交+轮询", kind: "custom", author: "pollo", version: "1.2.0", mediaType: "video", path: "/v1/video/generations", refCount: 2, updatedAt: "2026-08-21" },
  { key: "ce-91b0de", name: "Segmind PixelFlow", kind: "custom", author: "pollo", version: "0.4.1", mediaType: "video", path: "/pixelflow/run", refCount: 0, updatedAt: "2026-08-25" },
];

/** 样例定义（按 #2123 最小字段集 + #2143 补丁），A 的 JSON 视图与 B 的编辑器共用。 */
export const SAMPLE_DEFINITION = `{
  "kind": "declarative",
  "schema_version": "1.0.0",
  "meta": {
    "name": "偶得视频 · 提交+轮询",
    "author": "pollo",
    "version": "1.2.0",
    "hints": {
      "base_url": "https://api.oude.example.com",
      "models": ["oude-video-std", "oude-video-pro"]
    }
  },
  "auth": {
    "headers": { "Authorization": "Bearer {{ api_key }}" }
  },
  "inputs": {
    "image": { "source": "first_frame", "encoding": "data_uri" }
  },
  "submit": {
    "url": "{{ base_url }}/v1/video/generations",
    "body": {
      "model": "{{ model }}",
      "prompt": "{{ prompt }}",
      "image": "{{ image }}",
      "duration": "{{ duration_seconds }}",
      "size": "{{ width }}x{{ height }}"
    },
    "extract": { "task_id": ["$.id", "$.data.task_id"] }
  },
  "poll": {
    "url": "{{ base_url }}/v1/video/generations/{{ task_id }}",
    "extract": {
      "status": ["$.status"],
      "video_url": ["$.data.video_url", "$.video_url"],
      "error": ["$.error.message", "$.message"],
      "usage": { "duration_seconds": ["$.data.duration"] }
    }
  },
  "status_map": {
    "queued": "queued",
    "processing": "running",
    "succeeded": "succeeded",
    "failed": "failed"
  },
  "capabilities": {
    "text_to_video": true,
    "image_to_video": true
  }
}`;

/** 验证响应模式（check-response）的逐阶段提取 mock 结果。 */
export const MOCK_CHECK_RESULT = [
  { field: "status", path: "$.status", hit: true, value: '"processing" → running' },
  { field: "video_url", path: "$.data.video_url", hit: true, value: '"https://cdn.oude.example.com/v/9f21.mp4"' },
  { field: "error", path: "$.error.message", hit: false, value: "无命中（两条路径均落空，正常：成功响应无 error）" },
  { field: "usage.duration_seconds", path: "$.data.duration", hit: true, value: "6.0" },
];

/** 测试连接模式（trial-runs）的轮询时间线 mock。 */
export const MOCK_TRIAL_TIMELINE = [
  { t: "00:00", label: "提交", detail: "POST /v1/video/generations → 200 · task_id=vg-20260827-9f21" },
  { t: "00:05", label: "轮询 #1", detail: "status=queued" },
  { t: "00:10", label: "轮询 #2", detail: "status=processing" },
  { t: "00:52", label: "轮询 #9", detail: "status=succeeded · video_url 命中 $.data.video_url" },
  { t: "00:55", label: "下载", detail: "同源，附带凭证 · 14.2 MB · 完成" },
];

/** 预览请求模式（preview-request）渲染出的请求 mock。 */
export const MOCK_PREVIEW_REQUEST = `POST https://api.oude.example.com/v1/video/generations
Authorization: Bearer sk-****************8f2a
Content-Type: application/json

{
  "model": "oude-video-std",
  "prompt": "雨夜霓虹街道，一只机械猫穿过水洼",
  "image": "data:image/jpeg;base64,/9j/4AAQ…（首帧素材，已折叠）",
  "duration": 6,
  "size": "1280x720"
}`;
