---
name: adapt-custom-endpoint
description: 为 JSON 提交并轮询的视频供应商编写、验证、测试和保存 ArcReel 自定义调用端点定义。用户提供供应商 API 文档或要求接入自定义视频协议时使用。
---

# 适配自定义调用端点

把供应商文档转换成 ArcReel 声明式定义，并通过既有 HTTP API 验证和保存。使用
`scripts/custom_endpoint.py`，不要自行实现校验器或直接操作数据库。

## 工作流

1. 用 WebFetch 读取用户提供的供应商文档 URL；无法读取时请用户粘贴提交、查询任务与响应示例。
   确认协议属于 JSON 请求/响应的「提交后轮询」形态。签名鉴权、multipart 请求或按素材切换路由
   无法由首期定义表达，直接说明缺口。
2. 需要编写或修正定义时读取[定义格式](references/definition-format.md)，在工作目录创建定义 JSON、
   测试参数 JSON，以及供应商提供的真实响应样本。凭证优先引用 ArcReel 已保存的自定义供应商
   `provider_id`；不得把 API Key 写入项目文件、命令参数或回复。
3. 运行 `python .claude/skills/adapt-custom-endpoint/scripts/custom_endpoint.py validate <definition.json>`。
   修正到 `errors` 为空。`schema_version` 提示、`warnings` 与 `hints` 自行处理，并向用户转述取舍。
4. 对供应商的提交与轮询响应分别运行 `check-response`。这一步离线且免费，应先于出站测试。
5. 运行 `preview-request`，核对 URL、method、打码后的 headers、body 与素材摘要。
6. **测试连接会真实请求供应商并可能计费。调用前必须回问用户。** 获得明确同意后才运行
   `trial-run ... --confirm-cost`，再用 `trial-status <run-id>` 查询到终态，并转述请求、响应、取值与错误。
7. 再次 validate。没有同血统端点时用 `save` 新建；有重复时默认另存副本并告知用户。
   **只有覆盖既有端点必须回问用户**；明确同意后才用
   `save ... --endpoint-id <id> --confirm-overwrite`。保存成功以返回 `ce-<id>` 为完成判据。

## 连接

脚本从环境读取 `ARCREEL_API_BASE` 与 `ARCREEL_API_TOKEN`。内嵌 Agent 会话自动注入 localhost API
与短期 JWT；外部 Agent 使用 ArcReel 设置页创建的 `arc-` API Key，并通过宿主的秘密环境变量注入。
`AUTH_ENABLED=false` 的本地部署可留空 token。运行 `--help` 查看各命令参数。
