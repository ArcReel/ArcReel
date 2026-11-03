"""Russian translations for provider registry metadata."""

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
    "provider_name_agnes": "Agnes",
    # 失败文案里目录查不到的供应商（已被删除）的泛称
    "deleted_provider_display_name": "Удалённый провайдер",
    "deleted_agent_provider_display_name": "Удалённый провайдер агентов",
    # Provider descriptions
    "provider_desc_gemini-aistudio": "Google AI Studio предоставляет модели Gemini с генерацией изображений, видео и речи — подходит для быстрого прототипирования и личных проектов.",
    "provider_desc_gemini-vertex": "Корпоративная платформа Google Cloud Vertex AI поддерживает модели Gemini и Imagen с более высокими квотами и генерацией аудио.",
    "provider_desc_ark": "AI-платформа ByteDance Volcengine Ark поддерживает генерацию видео Seedance и изображений Seedream, а также генерацию аудио и управление seed.",
    "provider_desc_ark-agent-plan": "Volcengine Ark Agent Plan объединяет Doubao и другие ведущие модели для генерации текста, изображений и видео.",
    "provider_desc_grok": "Модели xAI Grok поддерживают генерацию видео и изображений.",
    "provider_desc_openai": "Платформа OpenAI поддерживает текст GPT-5.4 и генерацию изображений GPT Image.",
    "provider_desc_vidu": "Видеоплатформа Shengshu Vidu поддерживает генерацию видео из текста и из изображения, по первому и последнему кадру и по референсу, а также изображение по референсу. Только изображения и видео.",
    "provider_desc_dashscope": "Мультимодальная платформа Alibaba Cloud Model Studio (DashScope) поддерживает текст Qwen, изображения Qwen-Image / Wan и видео HappyHorse / Wan (включая видео по референсу).",
    "provider_desc_minimax": "Мультимодальная платформа MiniMax (Hailuo) с генерацией текста, изображений, видео и речи. По умолчанию подключается к китайскому сайту; для работы из-за рубежа укажите в base_url международный сайт.",
    "provider_desc_kling": "Платформа генерации видео и изображений Kuaishou Kling. Аутентификация по API-ключу работает со всеми моделями; Access Key + Secret Key (JWT) поддерживает только модели 3.0 и старше. Выберите один способ — если заданы оба, приоритет у API-ключа.",
    "provider_desc_agnes": "Мультимодальная платформа Agnes (в стиле OpenAI) с аутентификацией по Bearer API-ключу; сейчас поддерживает генерацию изображений, текста и видео.",
    # Agent preset notes (lib/agent/agent_provider_catalog.py)
    "preset_notes_deepseek": "Официальная конечная точка DeepSeek с совместимостью с Anthropic; нужен ключ с префиксом sk-.",
    "preset_notes_xiaomi_mimo": "Xiaomi MiMo принимает только известные имена моделей; публичного списка моделей нет.",
    "preset_notes_ark_coding_plan": "Volcengine Ark Coding Plan; эндпоинта списка моделей нет — выберите предложенную модель, включённую в консоли Ark.",
    "preset_notes_ark_agent_plan": "Volcengine Ark Agent Plan; эндпоинта списка моделей нет — выберите предложенную модель, входящую в ваш тариф.",
}
