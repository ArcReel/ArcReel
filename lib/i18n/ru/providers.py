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
    # Generic label in failure copy for deleted providers absent from the catalog.
    "deleted_provider_display_name": "Удалённый провайдер",
    "deleted_agent_provider_display_name": "Удалённый провайдер агентов",
    # Provider descriptions state only what the platform is, sourced from docs/api-docs/providers/<id>.md;
    # omit model versions/generation-modality lists: capability tags derive live from catalogs, so descriptions would become stale;
    # model-family names (GPT, Qwen, etc.) identify platforms and may be included.
    "provider_desc_gemini-aistudio": "Веб-среда разработки от Google для прототипирования приложений на генеративных моделях ИИ.",
    "provider_desc_gemini-vertex": "Сервис машинного обучения Google Cloud, теперь переименованный в Gemini Enterprise Agent Platform.",
    "provider_desc_ark": "Платформа сервисов больших моделей (MaaS) от Volcengine, облака ByteDance: дообучение, оценка и инференс моделей для бизнеса.",
    "provider_desc_ark-agent-plan": "Подписочный тариф сервисов моделей Volcengine Ark для частных пользователей с оплатой кредитами.",
    "provider_desc_grok": "Grok API от SpaceX Super Intelligence (ранее xAI) с генеративными моделями ИИ семейства Grok.",
    "provider_desc_openai": "API-платформа американской компании OpenAI с генеративными моделями ИИ серии GPT и другими.",
    "provider_desc_vidu": "API-платформа Vidu компании ShengShu Technology для генерации видео с помощью ИИ.",
    "provider_desc_dashscope": "Платформа сервисов моделей Alibaba Cloud: Qwen и другие модели с доступом по API, дообучением и развёртыванием.",
    "provider_desc_minimax": "Открытая платформа шанхайской ИИ-компании MiniMax с её мультимодальными моделями и генерацией видео Hailuo. По умолчанию подключается к китайскому сайту; для работы из-за рубежа укажите в base_url международный сайт.",
    "provider_desc_kling": "Открытая платформа Kling AI, сервиса генеративного ИИ компании Kuaishou. Аутентификация по API-ключу работает со всеми моделями; Access Key + Secret Key (JWT) поддерживает только модели версии 3.0 и более ранние. Выберите один способ — если заданы оба, приоритет у API-ключа.",
    "provider_desc_agnes": "Мультимодальная ИИ-платформа Agnes AI с генеративными моделями ИИ через единый API.",
    # Agent preset notes (lib/agent/agent_provider_catalog.py)
    "preset_notes_deepseek": "Официальная конечная точка DeepSeek с совместимостью с Anthropic; нужен ключ с префиксом sk-.",
    "preset_notes_xiaomi_mimo": "Xiaomi MiMo принимает только известные имена моделей; публичного списка моделей нет.",
    "preset_notes_ark_coding_plan": "Volcengine Ark Coding Plan; эндпоинта списка моделей нет — выберите предложенную модель, включённую в консоли Ark.",
    "preset_notes_ark_agent_plan": "Volcengine Ark Agent Plan; эндпоинта списка моделей нет — выберите предложенную модель, входящую в ваш тариф.",
}
