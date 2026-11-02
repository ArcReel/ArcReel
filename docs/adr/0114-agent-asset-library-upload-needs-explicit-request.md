---
status: accepted
---

# Agent 素材库工具复用 Web 编排，上传须声明用户明确要求，且立即返回

Agent 需要查询、上传、选择已有素材与解除关联。这四种操作都在 `server/agent_toolset/asset_library.py` 声明一次，两宿主经同一声明投影（[ADR 0087](0087-agent-toolset-single-declaration.md)）。handler 调用 Web 路由使用的 `server/services/project/provider_asset_links.py` 与 `provider_asset_import.py`。关联、账号与范围的判断只在 `lib/asset_library/links.py` 实现，工具结果的字段与 Web 响应相同，错误码沿用领域异常的 i18n key。

上传会让用户的图片进入供应商账号，有时还会产生费用。生成被供应商审核拒绝后，Agent 容易把上传当成自动补救。仅靠描述约束不够，所以上传的请求模型要求必填 `user_requested=true`，缺少时在入口返回 `invalid_request`，不创建入库任务；工具描述、内嵌 Agent 配置源与外部 skill 用同一说法约束：生成被拒不构成授权，也不授权自动重新生成。这个字段只防止 Agent 未经思考调用，不能证明用户确实提出过要求；能否上传仍以对话里用户的明确指令为准。

入库与审核可能持续数分钟到数小时。上传和刷新只写记录、唤醒后台循环后立即返回，两宿主都不等待；内嵌宿主不沿用生成批次的阻塞等待，因为供应商审核没有可预期的终态时间，阻塞会占住会话。之后由 Agent 按用户需要调 `get_provider_asset_links` 查询状态。

不提供供应商素材的远端改名、删除，也不提供素材库账号、对象存储与凭证绑定的配置：前者在供应商控制台管理，后者只在设置页输入，工具结果不含密钥、中转对象或签名下载地址。

## 后果

用户在对话里要求上传后，Agent 需要再查询一次才能知道入库是否可用，不能在同一次调用里拿到结果。从素材库选择已有素材的 Agent 写入标记为 `worker` 来源，与其他 Agent 写入一致。范围与验收见 [Spec #3559](https://github.com/ArcReel/ArcReel/issues/3559)。
