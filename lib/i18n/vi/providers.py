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
    # 失败文案里目录查不到的供应商（已被删除）的泛称
    "deleted_provider_display_name": "Nhà cung cấp đã bị xóa",
    "deleted_agent_provider_display_name": "Nhà cung cấp Agent đã bị xóa",
    # Provider descriptions：只写平台是什么，取自 docs/api-docs/providers/<id>.md 记录的来源；
    # 不写模型版本与生成模态清单，能力标签由模型目录实时得出，写进描述必然过时；
    # 模型家族名（GPT、Qwen 等）属于平台身份，可以写。
    "provider_desc_gemini-aistudio": "Môi trường phát triển tích hợp trên web do Google phát triển để tạo nguyên mẫu ứng dụng với các mô hình AI tạo sinh.",
    "provider_desc_gemini-vertex": "Dịch vụ học máy do Google Cloud cung cấp, nay đã đổi tên thành Gemini Enterprise Agent Platform.",
    "provider_desc_ark": "Nền tảng dịch vụ mô hình lớn (MaaS) của Volcengine thuộc ByteDance, cung cấp tinh chỉnh, đánh giá và suy luận mô hình cho doanh nghiệp.",
    "provider_desc_ark-agent-plan": "Gói dịch vụ mô hình lớn theo thuê bao của Volcengine Ark dành cho người dùng cá nhân, tính phí theo điểm.",
    "provider_desc_grok": "Grok API của SpaceX Super Intelligence (trước đây là xAI), cung cấp dòng mô hình AI tạo sinh Grok.",
    "provider_desc_openai": "Nền tảng API của OpenAI, công ty trí tuệ nhân tạo của Mỹ, cung cấp dòng GPT và các mô hình AI tạo sinh khác.",
    "provider_desc_vidu": "Nền tảng Vidu API của ShengShu Technology, cung cấp khả năng tạo video bằng AI.",
    "provider_desc_dashscope": "Nền tảng dịch vụ mô hình lớn của Alibaba Cloud, cung cấp Qwen và các mô hình khác qua API, tinh chỉnh và triển khai.",
    "provider_desc_minimax": "Nền tảng mở của MiniMax, công ty AI có trụ sở tại Thượng Hải, cung cấp các mô hình đa phương thức và dịch vụ tạo video Hailuo. Mặc định kết nối site nội địa; đặt base_url sang site quốc tế khi dùng ở nước ngoài.",
    "provider_desc_kling": "Nền tảng mở của Kling AI, dịch vụ AI tạo sinh của Kuaishou. Xác thực bằng API Key áp dụng cho mọi model; Access Key + Secret Key (JWT) chỉ áp dụng cho model từ 3.0 trở về trước. Chọn một trong hai — nếu điền cả hai, API Key được ưu tiên.",
    "provider_desc_agnes": "Nền tảng AI đa phương thức của Agnes AI, cung cấp các mô hình AI tạo sinh của hãng qua một API thống nhất.",
    # Agent preset notes (lib/agent/agent_provider_catalog.py)
    "preset_notes_deepseek": "Endpoint Anthropic-compat chính thức của DeepSeek; cần API key sk-.",
    "preset_notes_xiaomi_mimo": "Xiaomi MiMo chỉ chấp nhận tên model đã biết; không có danh sách model công khai.",
    "preset_notes_ark_coding_plan": "Gói Volcengine Ark Coding Plan; không có endpoint danh sách model — hãy chọn model gợi ý đã bật trong bảng điều khiển Ark.",
    "preset_notes_ark_agent_plan": "Gói Volcengine Ark Agent Plan; không có endpoint danh sách model — hãy chọn model gợi ý có trong gói của bạn.",
}
