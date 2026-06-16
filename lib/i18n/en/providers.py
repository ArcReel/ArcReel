"""English translations for provider registry metadata."""

MESSAGES: dict[str, str] = {
    # Provider display names
    "provider_name_gemini-aistudio": "AI Studio",
    "provider_name_gemini-vertex": "Vertex AI",
    "provider_name_ark": "Volcengine Ark",
    "provider_name_ark-agent-plan": "Volcengine Ark Agent Plan",
    "provider_name_grok": "Grok",
    "provider_name_openai": "OpenAI",
    "provider_name_vidu": "Vidu",
    "provider_name_dashscope": "Alibaba Model Studio",
    "provider_name_minimax": "MiniMax",
    "provider_name_kling": "Kling",
    # Provider descriptions
    "provider_desc_gemini-aistudio": "Google AI Studio provides Gemini models with image and video generation, ideal for rapid prototyping and personal projects.",
    "provider_desc_gemini-vertex": "Google Cloud Vertex AI enterprise platform supporting Gemini and Imagen models with higher quotas and audio generation.",
    "provider_desc_ark": "ByteDance Volcengine Ark AI platform supporting Seedance video generation and Seedream image generation, with audio and seed control.",
    "provider_desc_ark-agent-plan": "Volcengine Ark Agent Plan aggregates Doubao and other major models for text, image and video generation.",
    "provider_desc_grok": "xAI Grok models supporting video and image generation.",
    "provider_desc_openai": "OpenAI platform supporting GPT-5.4 text, GPT Image and Sora video generation.",
    "provider_desc_vidu": "Shengshu Vidu video platform supporting text-to-video, image-to-video, first-last frame, reference-to-video and reference-to-image. Image and video only.",
    "provider_desc_dashscope": "Alibaba Cloud Model Studio (DashScope) full-modality platform supporting Qwen text, Qwen-Image / Wan images, and HappyHorse / Wan video (including reference-to-video).",
    "provider_desc_minimax": "MiniMax (Hailuo) multimodal platform with text, image and video generation. Connects to the domestic site by default; set base_url to the international site for overseas access.",
    "provider_desc_kling": "Kuaishou Kling video and image generation platform, authenticated with an Access Key and Secret Key.",
    # Agent preset notes (lib/agent_provider_catalog.py)
    "preset_notes_deepseek": "DeepSeek official Anthropic-compat endpoint; needs sk- prefixed key.",
    "preset_notes_xiaomi_mimo": "Xiaomi MiMo only accepts known model names; no public model list.",
    "preset_notes_ark_coding_plan": "Volcengine Ark Coding Plan",
    "preset_notes_ark_agent_plan": "Volcengine Ark Agent Plan",
}
