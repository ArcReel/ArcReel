"""Vietnamese translations for provider registry metadata."""

MESSAGES: dict[str, str] = {
    # Provider display names
    "provider_name_gemini-aistudio": "AI Studio",
    "provider_name_gemini-vertex": "Vertex AI",
    "provider_name_ark": "Volcengine Ark",
    "provider_name_grok": "Grok",
    "provider_name_openai": "OpenAI",
    "provider_name_vidu": "Vidu",
    # Provider descriptions
    "provider_desc_gemini-aistudio": "Google AI Studio cung cấp các mô hình Gemini hỗ trợ tạo ảnh và video, phù hợp cho việc dựng prototype nhanh và dự án cá nhân.",
    "provider_desc_gemini-vertex": "Nền tảng doanh nghiệp Vertex AI của Google Cloud hỗ trợ các mô hình Gemini và Imagen với hạn mức cao hơn cùng khả năng tạo âm thanh.",
    "provider_desc_ark": "Nền tảng AI Volcengine Ark của ByteDance hỗ trợ tạo video Seedance và tạo ảnh Seedream, kèm âm thanh và điều khiển seed.",
    "provider_desc_grok": "Các mô hình Grok của xAI hỗ trợ tạo video và tạo ảnh.",
    "provider_desc_openai": "Nền tảng OpenAI hỗ trợ văn bản GPT-5.4, GPT Image và tạo video Sora.",
    "provider_desc_vidu": "Nền tảng Vidu của Shengshu hỗ trợ tạo video từ văn bản, từ ảnh, khung đầu–cuối, video tham chiếu và ảnh tham chiếu. Chỉ hỗ trợ ảnh và video.",
    # Agent preset notes (lib/agent_provider_catalog.py)
    "preset_notes_deepseek": "Endpoint Anthropic-compat chính thức của DeepSeek; cần API key sk-.",
    "preset_notes_xiaomi_mimo": "Xiaomi MiMo chỉ chấp nhận tên model đã biết; không có danh sách model công khai.",
}
