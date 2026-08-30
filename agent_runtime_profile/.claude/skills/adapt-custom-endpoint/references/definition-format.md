# ArcReel 自定义调用端点定义格式

服务端共享 validator 是保存、预览请求、验证响应和测试连接的最终判据。当前完整 JSON Schema 位于
[`lib/custom_provider/endpoint_definition/schema.json`](https://github.com/ArcReel/ArcReel/blob/main/lib/custom_provider/endpoint_definition/schema.json)；
先写最小定义，再根据 `validate` 返回的字段路径与错误码修正。

## 最小形状

```json
{
  "kind": "declarative",
  "schema_version": "1.0.0",
  "meta": {"name": "Demo Video", "author": "user", "version": "1.0.0"},
  "auth": {"headers": {"Authorization": "Bearer {{ api_key }}"}},
  "submit": {
    "method": "POST",
    "url": "{{ base_url }}/videos",
    "body": {"model": "{{ model }}", "prompt": "{{ prompt }}", "duration": "{{ duration }}"},
    "extract": {"task_id": ["$.id"], "error": ["$.error.message", "$.message"]}
  },
  "poll": {
    "method": "GET",
    "url": "{{ base_url }}/videos/{{ task_id }}",
    "extract": {
      "status": ["$.status"],
      "video_url": ["$.output.url"],
      "error": ["$.error.message", "$.message"]
    }
  },
  "status_map": {
    "queued": "queued",
    "processing": "running",
    "completed": "succeeded",
    "failed": "failed"
  },
  "capabilities": {"text_to_video": true}
}
```

## 约束

- `auth` 可为空；非空时至少一处引用 `{{ api_key }}`，凭证只能出现在 `auth.headers` 或
  `auth.query`。普通 request headers、body 与 URL 不得另写凭证。
- 模板变量包括 `base_url`、`api_key`（仅 auth）、`model`、`prompt`、`duration`、
  `duration_seconds`、`aspect_ratio`、`resolution`、`generate_audio`、`seed`、`width`、`height`、
  `task_id`、`result_id` 与 `inputs.<name>`。整串单占位符保留原类型；值为 null 时删除所在字段。
- 素材在 `inputs` 声明，`source` 取 `start_image`、`end_image`、`reference_images` 或
  `reference_audio_files`，`encoding` 取 `data_uri` 或 `base64`。列表素材用 `$each` 展开；可选对象
  用 `$when` 守卫。`capabilities` 必须与 submit 实际引用的素材双向一致。
- `enum_maps` 把 ArcReel 参数值映射到供应商值；`defaults` 只为可选参数填 ArcReel 侧缺省值。
- `extract` 是按优先级排列的 JSONPath 数组。路径从 `$` 开始，只用 child segment 与简单过滤；
  不用递归下降、联合、带 step 的切片或函数。响应字段包含 JSON 字符串时使用
  `{"path":"$.data","json_decode":true,"then":["$.id"]}`。
- `status_map` 的目标只取 `queued`、`running`、`succeeded`、`failed`。若成功后还需二次取件，
  在 poll 提取 `result_id`，并增加 `result` 节从 `task_id` / `result_id` 获取 `video_url`。

## HTTP CLI 请求形状

```bash
python .claude/skills/adapt-custom-endpoint/scripts/custom_endpoint.py validate definition.json
python .claude/skills/adapt-custom-endpoint/scripts/custom_endpoint.py check-response definition.json --stage submit --response submit-response.json
python .claude/skills/adapt-custom-endpoint/scripts/custom_endpoint.py preview-request definition.json --parameters parameters.json --credentials credentials-ref.json --start-image start.png
python .claude/skills/adapt-custom-endpoint/scripts/custom_endpoint.py trial-run definition.json --parameters parameters.json --credentials credentials-ref.json --reference-images subject-1.png --reference-images subject-2.png --confirm-cost
python .claude/skills/adapt-custom-endpoint/scripts/custom_endpoint.py trial-status RUN_ID
python .claude/skills/adapt-custom-endpoint/scripts/custom_endpoint.py save definition.json
```

`parameters.json` 与 API 同名，例如：

```json
{"model":"provider-model","prompt":"short test","duration_seconds":5,"aspect_ratio":"9:16","generate_audio":false}
```

`credentials-ref.json` 优先只引用已保存的供应商：

```json
{"provider_id":"custom-1"}
```

素材参数与 multipart 字段一一对应：`--start-image` → `start_image`、`--end-image` →
`end_image`、`--reference-images` → `reference_images`、`--reference-audio-files` →
`reference_audio_files`。后两项可重复传入多个文件；预览请求与测试连接使用同一组参数。
