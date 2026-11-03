"""Chinese translations for provider registry metadata."""

MESSAGES: dict[str, str] = {
    # Provider display names
    "provider_name_gemini-aistudio": "AI Studio",
    "provider_name_gemini-vertex": "Vertex AI",
    "provider_name_ark": "火山方舟",
    "provider_name_ark-agent-plan": "火山方舟 Agent Plan",
    "provider_name_grok": "Grok",
    "provider_name_openai": "OpenAI",
    "provider_name_vidu": "Vidu",
    "provider_name_dashscope": "阿里百炼",
    "provider_name_minimax": "MiniMax",
    "provider_name_kling": "可灵 Kling",
    "provider_name_agnes": "Agnes",
    # 失败文案里目录查不到的供应商（已被删除）的泛称
    "deleted_provider_display_name": "已删除的供应商",
    "deleted_agent_provider_display_name": "已删除的 Agent 供应商",
    # Provider descriptions：只写平台是什么，取自 docs/api-docs/providers/<id>.md 记录的来源；
    # 不写模型版本与生成模态清单，能力标签由模型目录实时得出，写进描述必然过时；
    # 模型家族名（GPT、Qwen 等）属于平台身份，可以写。
    "provider_desc_gemini-aistudio": "Google 开发的网页端集成开发环境，用于基于生成式 AI 模型设计应用原型。",
    "provider_desc_gemini-vertex": "Google Cloud 提供的机器学习服务，已更名为 Gemini Enterprise Agent Platform。",
    "provider_desc_ark": "字节跳动旗下火山引擎的大模型服务平台（MaaS），面向企业提供模型精调、评测与推理等服务。",
    "provider_desc_ark-agent-plan": "火山方舟面向个人用户推出的订阅式大模型服务套餐，按积分计费。",
    "provider_desc_grok": "SpaceX Super Intelligence（原 xAI）的 Grok API，提供其 Grok 系列生成式 AI 模型。",
    "provider_desc_openai": "美国人工智能公司 OpenAI 的 API 平台，提供其 GPT 系列等生成式 AI 模型。",
    "provider_desc_vidu": "生数科技的 Vidu API 平台，提供 AI 视频生成能力。",
    "provider_desc_dashscope": "阿里云的大模型服务平台，提供通义千问（Qwen）等模型的 API 调用、精调与部署。",
    "provider_desc_minimax": "上海人工智能公司 MiniMax（稀宇科技）的开放平台，提供其多模态模型与海螺视频生成服务。默认连接国内站，海外可将 base_url 切换到国际站。",
    "provider_desc_kling": "快手推出的生成式 AI 服务可灵的开放平台。API Key 鉴权适用于全部模型；Access Key + Secret Key（JWT）仅适用于 3.0 及更早模型，二者二选一，同时填写时 API Key 优先。",
    "provider_desc_agnes": "Agnes AI 的多模态 AI 平台，通过统一的 API 提供其生成式 AI 模型。",
    # Agent preset notes (lib/agent/agent_provider_catalog.py)
    "preset_notes_deepseek": "DeepSeek 官方 Anthropic 兼容端点，需 sk- 开头的 API Key",
    "preset_notes_xiaomi_mimo": "小米 MiMo 仅支持已知模型名，未公开模型列表",
    "preset_notes_ark_coding_plan": "火山方舟 Coding Plan 套餐；不提供模型列表接口，请从建议模型中选择已在控制台开通的模型",
    "preset_notes_ark_agent_plan": "火山方舟 Agent Plan 套餐；不提供模型列表接口，请从建议模型中选择当前套餐已开通的模型",
}
