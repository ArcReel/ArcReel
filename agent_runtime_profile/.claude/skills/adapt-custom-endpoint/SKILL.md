---
name: adapt-custom-endpoint
description: 当用户要求把采用 JSON 提交后轮询协议的视频供应商接入 ArcReel，或要求编写、验证、测试、保存自定义调用端点定义时使用。
---

# 适配自定义调用端点

使用随 skill 提供的 `scripts/custom_endpoint.py` 调用 ArcReel HTTP API；共享 validator 和 API 是验证与保存定义的唯一入口。

## 工作流

1. 读取用户提供的供应商文档 URL；无法读取时请用户粘贴提交、查询任务与响应示例。
   确认协议属于 JSON 请求/响应的「提交后轮询」形态。签名鉴权、multipart 请求或按素材切换路由
   无法由首期定义表达，直接说明缺口。
2. 需要编写或修正定义时读取[定义格式](references/definition-format.md)，在工作目录创建定义 JSON、
   测试参数 JSON，以及供应商提供的真实响应样本。凭证优先引用 ArcReel 已保存的自定义供应商
   `provider_id`；不得把 API Key 写入项目文件、命令参数或回复。
3. 运行当前 skill 目录下的 `scripts/custom_endpoint.py`，用 `validate <definition.json>` 子命令校验。
   修正到 `errors` 为空。`schema_version.level` 非 `direct` 或仍有 `warnings` 时，修正或向用户说明接受的原因和影响；用 `hints` 准备测试所需的接口地址和模型。
4. 对供应商的提交与轮询响应分别运行 `check-response`；定义包含 `result` 时也检查取件响应。这一步离线且免费，应先于出站测试。
5. 运行 `preview-request`，核对 URL、method、打码后的 headers、body 与素材摘要。定义有必需素材时，
   用 `--start-image` / `--end-image` / `--reference-images` / `--reference-audio-files` 附上对应文件。
6. **测试连接会真实请求供应商并可能计费。调用前必须回问用户。** 获得明确同意后才运行
   `trial-run ... --confirm-cost`（沿用预览时的素材参数），再用 `trial-status <run-id>` 查询到终态，
   并转述请求、响应、取值与错误。状态为 `succeeded` 时继续；其他终态停止，只有用户明确要求保存未验证定义时才继续。
7. 再次 validate。没有同血统端点时用 `save` 新建；有重复时默认另存副本并告知用户。
   **只有覆盖既有端点必须回问用户**；明确同意后才用
   `save ... --endpoint-id <id> --confirm-overwrite`。保存成功以返回 `ce-<id>` 为完成判据。

## 连接

在当前工作区根目录运行脚本。外部 Agent 读取 `setup-arcreel-skills` 创建的
`.arcreel/settings.json`，从 `mcp_url` 派生同实例的 `/api/v1` 地址，并用 `api_key` 鉴权；配置缺失或
无效时先执行 setup skill，不回退到 localhost。

内嵌 Agent 会话通过 `ARCREEL_API_BASE` 与 `ARCREEL_API_TOKEN` 自动注入 localhost API 和短期 JWT，
优先于工作区配置；该 JWT 有效期 15 分钟且不续期，会话超时后 API 调用会以 401 失败，此时告知用户
重开会话获取新 token。`AUTH_ENABLED=false` 的本地部署可留空 token。运行 `--help` 查看各命令参数。
