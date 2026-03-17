"""
费用计算器

基于 docs/视频&图片生成费用表.md 中的费用规则，计算图片和视频生成的费用。
支持按模型区分费用，以便不同模型的历史数据能正确计费。
"""


class CostCalculator:
    """费用计算器"""

    # 图片费用（美元/张），按模型和分辨率区分
    IMAGE_COST = {
        "gemini-3-pro-image-preview": {
            "1K": 0.134,
            "2K": 0.134,
            "4K": 0.24,
        },
        "gemini-3.1-flash-image-preview": {
            "512PX": 0.045,
            "1K": 0.067,
            "2K": 0.101,
            "4K": 0.151,
        },
    }

    DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image-preview"

    # 视频费用（美元/秒），按模型区分
    # 格式：model -> {(resolution, generate_audio): cost_per_second}
    VIDEO_COST = {
        "veo-3.1-generate-001": {
            ("720p", True): 0.40,
            ("720p", False): 0.20,
            ("1080p", True): 0.40,
            ("1080p", False): 0.20,
            ("4k", True): 0.60,
            ("4k", False): 0.40,
        },
        "veo-3.1-fast-generate-001": {
            ("720p", True): 0.15,
            ("720p", False): 0.10,
            ("1080p", True): 0.15,
            ("1080p", False): 0.10,
            ("4k", True): 0.35,
            ("4k", False): 0.30,
        },
        # 历史兼容：preview 模型已下线，保留费率供历史计费使用
        "veo-3.1-generate-preview": {
            ("720p", True): 0.40,
            ("720p", False): 0.20,
            ("1080p", True): 0.40,
            ("1080p", False): 0.20,
            ("4k", True): 0.60,
            ("4k", False): 0.40,
        },
        "veo-3.1-fast-generate-preview": {
            ("720p", True): 0.15,
            ("720p", False): 0.10,
            ("1080p", True): 0.15,
            ("1080p", False): 0.10,
            ("4k", True): 0.35,
            ("4k", False): 0.30,
        },
    }

    SELECTABLE_VIDEO_MODELS = [
        "veo-3.1-generate-001",
        "veo-3.1-fast-generate-001",
    ]

    DEFAULT_VIDEO_MODEL = "veo-3.1-generate-001"

    # Seedance 视频费用（元/百万 token），按 (service_tier, generate_audio) 查表
    SEEDANCE_VIDEO_COST = {
        "doubao-seedance-1-5-pro-251215": {
            ("default", True): 16.00,
            ("default", False): 8.00,
            ("flex", True): 8.00,
            ("flex", False): 4.00,
        },
    }

    DEFAULT_SEEDANCE_MODEL = "doubao-seedance-1-5-pro-251215"

    def calculate_seedance_video_cost(
        self,
        usage_tokens: int,
        service_tier: str = "default",
        generate_audio: bool = True,
        model: str | None = None,
    ) -> tuple[float, str]:
        """
        计算 Seedance 视频生成费用。

        Returns:
            (amount, currency) — 金额和币种 (CNY)
        """
        model = model or self.DEFAULT_SEEDANCE_MODEL
        model_costs = self.SEEDANCE_VIDEO_COST.get(
            model, self.SEEDANCE_VIDEO_COST[self.DEFAULT_SEEDANCE_MODEL]
        )
        key = (service_tier, generate_audio)
        price_per_million = model_costs.get(
            key,
            model_costs.get(("default", True), 16.00),
        )
        amount = usage_tokens / 1_000_000 * price_per_million
        return amount, "CNY"

    def calculate_image_cost(self, resolution: str = "1K", model: str = None) -> float:
        """
        计算图片生成费用

        Args:
            resolution: 图片分辨率 ('512PX', '1K', '2K', '4K')
            model: 模型名称，默认使用当前默认模型

        Returns:
            费用（美元）
        """
        model = model or self.DEFAULT_IMAGE_MODEL
        model_costs = self.IMAGE_COST.get(model, self.IMAGE_COST[self.DEFAULT_IMAGE_MODEL])
        default_cost = model_costs.get("1K") or self.IMAGE_COST[self.DEFAULT_IMAGE_MODEL]["1K"]
        return model_costs.get(resolution.upper(), default_cost)

    def calculate_video_cost(
        self,
        duration_seconds: int,
        resolution: str = "1080p",
        generate_audio: bool = True,
        model: str = None,
    ) -> float:
        """
        计算视频生成费用

        Args:
            duration_seconds: 视频时长（秒）
            resolution: 分辨率 ('720p', '1080p', '4k')
            generate_audio: 是否生成音频
            model: 模型名称，默认使用当前默认模型

        Returns:
            费用（美元）
        """
        model = model or self.DEFAULT_VIDEO_MODEL
        model_costs = self.VIDEO_COST.get(model, self.VIDEO_COST[self.DEFAULT_VIDEO_MODEL])
        resolution = resolution.lower()
        cost_per_second = model_costs.get(
            (resolution, generate_audio),
            model_costs.get(("1080p", True)) or self.VIDEO_COST[self.DEFAULT_VIDEO_MODEL][("1080p", True)],
        )
        return duration_seconds * cost_per_second


# 单例实例，方便使用
cost_calculator = CostCalculator()
