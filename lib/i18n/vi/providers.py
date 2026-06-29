"""Vietnamese translations for provider registry metadata."""

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
    # Provider descriptions
    "provider_desc_gemini-aistudio": "Google AI Studio cung cấp các mô hình Gemini hỗ trợ tạo ảnh và video, phù hợp cho việc dựng prototype nhanh và dự án cá nhân.",
    "provider_desc_gemini-vertex": "Nền tảng doanh nghiệp Vertex AI của Google Cloud hỗ trợ các mô hình Gemini và Imagen với hạn mức cao hơn cùng khả năng tạo âm thanh.",
    "provider_desc_ark": "Nền tảng AI Volcengine Ark của ByteDance hỗ trợ tạo video Seedance và tạo ảnh Seedream, kèm âm thanh và điều khiển seed.",
    "provider_desc_ark-agent-plan": "Gói Volcengine Ark Agent Plan tổng hợp Doubao và nhiều mô hình lớn chủ lưu, bao gồm văn bản, ảnh và video.",
    "provider_desc_grok": "Các mô hình Grok của xAI hỗ trợ tạo video và tạo ảnh.",
    "provider_desc_openai": "Nền tảng OpenAI hỗ trợ văn bản GPT-5.4, GPT Image và tạo video Sora.",
    "provider_desc_vidu": "Nền tảng Vidu của Shengshu hỗ trợ tạo video từ văn bản, từ ảnh, khung đầu–cuối, video tham chiếu và ảnh tham chiếu. Chỉ hỗ trợ ảnh và video.",
    "provider_desc_dashscope": "Nền tảng đa phương thức Alibaba Cloud Model Studio (DashScope) hỗ trợ văn bản Qwen, ảnh Qwen-Image / Wan và video HappyHorse / Wan (bao gồm video tham chiếu).",
    "provider_desc_minimax": "Nền tảng đa phương thức MiniMax (Hailuo) hỗ trợ tạo văn bản, ảnh và video. Mặc định kết nối site nội địa; đặt base_url sang site quốc tế khi dùng ở nước ngoài.",
    "provider_desc_kling": "Nền tảng tạo video và ảnh Kling của Kuaishou, xác thực bằng Access Key và Secret Key.",
    "provider_desc_agnes": "Nền tảng đa phương thức Agnes (phong cách OpenAI), xác thực bằng Bearer API key; hiện hỗ trợ tạo ảnh, văn bản và video.",
    # Agent preset notes (lib/agent_provider_catalog.py)
    "preset_notes_deepseek": "Endpoint Anthropic-compat chính thức của DeepSeek; cần API key sk-.",
    "preset_notes_xiaomi_mimo": "Xiaomi MiMo chỉ chấp nhận tên model đã biết; không có danh sách model công khai.",
    "preset_notes_ark_coding_plan": "Gói Volcengine Ark Coding Plan",
    "preset_notes_ark_agent_plan": "Gói Volcengine Ark Agent Plan",
}
