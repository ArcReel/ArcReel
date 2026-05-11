"""English translations for provider registry metadata."""

MESSAGES: dict[str, str] = {
    # Provider display names
    "provider_name_gemini-aistudio": "AI Studio",
    "provider_name_gemini-vertex": "Vertex AI",
    "provider_name_ark": "Volcengine Ark",
    "provider_name_grok": "Grok",
    "provider_name_openai": "OpenAI",
    "provider_name_vidu": "Vidu",
    # Provider descriptions
    "provider_desc_gemini-aistudio": "Google AI Studio provides Gemini models with image and video generation, ideal for rapid prototyping and personal projects.",
    "provider_desc_gemini-vertex": "Google Cloud Vertex AI enterprise platform supporting Gemini and Imagen models with higher quotas and audio generation.",
    "provider_desc_ark": "ByteDance Volcengine Ark AI platform supporting Seedance video generation and Seedream image generation, with audio and seed control.",
    "provider_desc_grok": "xAI Grok models supporting video and image generation.",
    "provider_desc_openai": "OpenAI platform supporting GPT-5.4 text, GPT Image and Sora video generation.",
    "provider_desc_vidu": "Shengshu Vidu video platform supporting text-to-video, image-to-video, first-last frame, reference-to-video and reference-to-image. Image and video only.",
    # Agent preset notes (lib/agent_provider_catalog.py)
    "preset_notes_deepseek": "DeepSeek official Anthropic-compat endpoint; needs sk- prefixed key.",
    "preset_notes_xiaomi_mimo": "Xiaomi MiMo only accepts known model names; no public model list.",
}
