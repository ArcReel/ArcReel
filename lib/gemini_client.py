"""
Gemini API 统一封装

提供文本生成的统一接口。图片/视频生成已迁移至 image_backends / video_backends。
"""

import asyncio
import functools
import logging
import random
import threading
import time
from collections import deque
from pathlib import Path
from typing import Dict, Optional, Tuple, Type, Union

from PIL import Image

from .cost_calculator import cost_calculator

logger = logging.getLogger(__name__)

# Vertex AI 服务账号所需 OAuth scopes（共享常量，供 gemini_client / video_backends / providers 复用）
VERTEX_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/generative-language",
]

# 可重试的错误类型
RETRYABLE_ERRORS: Tuple[Type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
)

# 尝试导入 Google API 错误类型
try:
    from google import genai  # Import genai to access its errors
    from google.api_core import exceptions as google_exceptions

    RETRYABLE_ERRORS = RETRYABLE_ERRORS + (
        google_exceptions.ResourceExhausted,  # 429 Too Many Requests
        google_exceptions.ServiceUnavailable,  # 503
        google_exceptions.DeadlineExceeded,  # 超时
        google_exceptions.InternalServerError,  # 500
        genai.errors.ClientError,  # 4xx errors from new SDK
        genai.errors.ServerError,  # 5xx errors from new SDK
    )
except ImportError:
    pass


class RateLimiter:
    """
    多模型滑动窗口限流器
    """

    def __init__(self, limits_dict: Dict[str, int] = None, *, request_gap: float = 3.1):
        """
        Args:
            limits_dict: {model_name: rpm} 字典。例如 {"gemini-3-pro-image-preview": 20}
            request_gap: 最小请求间隔（秒），默认 3.1
        """
        self.limits = limits_dict or {}
        self.request_gap = request_gap
        # 存储请求时间戳：{model_name: deque([timestamp1, timestamp2, ...])}
        self.request_logs: Dict[str, deque] = {}
        self.lock = threading.Lock()

    def acquire(self, model_name: str):
        """
        阻塞直到获得令牌
        """
        if model_name not in self.limits:
            return  # 该模型无限流配置

        limit = self.limits[model_name]
        if limit <= 0:
            return

        with self.lock:
            if model_name not in self.request_logs:
                self.request_logs[model_name] = deque()

            log = self.request_logs[model_name]

            while True:
                now = time.time()

                # 清理超过 60 秒的旧记录
                while log and now - log[0] > 60:
                    log.popleft()

                # 强制增加请求间隔（用户要求 > 3s）
                # 即使获得了令牌，也要确保距离上一次请求至少 3s
                # 获取最新的请求时间（可能是其他线程刚刚写入的）
                min_gap = self.request_gap
                if log:
                    last_request = log[-1]
                    gap = time.time() - last_request
                    if gap < min_gap:
                        time.sleep(min_gap - gap)
                        # 更新时间，重新检查
                        continue

                if len(log) < limit:
                    # 获取令牌成功
                    log.append(time.time())
                    return

                # 达到限制，计算等待时间
                # 等待直到最早的记录过期
                wait_time = 60 - (now - log[0]) + 0.1  # 多加 0.1s 缓冲
                if wait_time > 0:
                    time.sleep(wait_time)

    async def acquire_async(self, model_name: str):
        """
        异步阻塞直到获得令牌
        """
        if model_name not in self.limits:
            return  # 该模型无限流配置

        limit = self.limits[model_name]
        if limit <= 0:
            return

        while True:
            with self.lock:
                now = time.time()

                if model_name not in self.request_logs:
                    self.request_logs[model_name] = deque()

                log = self.request_logs[model_name]

                # 清理超过 60 秒的旧记录
                while log and now - log[0] > 60:
                    log.popleft()

                min_gap = self.request_gap
                wait_needed = 0
                if log:
                    last_request = log[-1]
                    gap = now - last_request
                    if gap < min_gap:
                        # 释放锁后异步等待
                        wait_needed = min_gap - gap

                if len(log) >= limit:
                    # 达到限制，计算等待时间
                    wait_needed = max(wait_needed, 60 - (now - log[0]) + 0.1)

                if wait_needed == 0 and len(log) < limit:
                    # 获取令牌成功
                    log.append(now)
                    return

            # 在锁外异步等待
            if wait_needed > 0:
                await asyncio.sleep(wait_needed)
            else:
                await asyncio.sleep(0.1)  # 短暂让出控制权


_SHARED_IMAGE_MODEL_NAME = cost_calculator.DEFAULT_IMAGE_MODEL
_SHARED_VIDEO_MODEL_NAME = cost_calculator.DEFAULT_VIDEO_MODEL

_shared_rate_limiter: Optional["RateLimiter"] = None
_shared_rate_limiter_lock = threading.Lock()


def _rate_limiter_limits_from_env(
    *,
    image_rpm: Optional[int] = None,
    video_rpm: Optional[int] = None,
    image_model: Optional[str] = None,
    video_model: Optional[str] = None,
) -> Dict[str, int]:
    if image_rpm is None:
        image_rpm = 15
    if video_rpm is None:
        video_rpm = 10
    if image_model is None:
        image_model = _SHARED_IMAGE_MODEL_NAME
    if video_model is None:
        video_model = _SHARED_VIDEO_MODEL_NAME

    limits: Dict[str, int] = {}
    if image_rpm > 0:
        limits[image_model] = image_rpm
    if video_rpm > 0:
        limits[video_model] = video_rpm
    return limits


def get_shared_rate_limiter(
    *,
    image_rpm: Optional[int] = None,
    video_rpm: Optional[int] = None,
    image_model: Optional[str] = None,
    video_model: Optional[str] = None,
    request_gap: Optional[float] = None,
) -> "RateLimiter":
    """
    获取进程内共享的 RateLimiter

    首次调用时根据参数或环境变量创建实例，后续调用返回同一实例。

    - image_rpm / video_rpm：每分钟请求数限制（None 时从环境变量读取）
    - request_gap：最小请求间隔（None 时从环境变量 GEMINI_REQUEST_GAP 读取，默认 3.1）
    """
    global _shared_rate_limiter
    if _shared_rate_limiter is not None:
        return _shared_rate_limiter

    with _shared_rate_limiter_lock:
        if _shared_rate_limiter is not None:
            return _shared_rate_limiter

        limits = _rate_limiter_limits_from_env(
            image_rpm=image_rpm,
            video_rpm=video_rpm,
            image_model=image_model,
            video_model=video_model,
        )
        if request_gap is None:
            request_gap = 3.1
        _shared_rate_limiter = RateLimiter(limits, request_gap=request_gap)
        return _shared_rate_limiter


def refresh_shared_rate_limiter(
    *,
    image_rpm: Optional[int] = None,
    video_rpm: Optional[int] = None,
    image_model: Optional[str] = None,
    video_model: Optional[str] = None,
    request_gap: Optional[float] = None,
) -> "RateLimiter":
    """
    Refresh the process-wide shared RateLimiter in-place.

    Updates model keys and request_gap. Parameters default to env vars when None.
    """
    limiter = get_shared_rate_limiter()
    new_limits = _rate_limiter_limits_from_env(
        image_rpm=image_rpm,
        video_rpm=video_rpm,
        image_model=image_model,
        video_model=video_model,
    )

    with limiter.lock:
        limiter.limits = new_limits
        if request_gap is not None:
            limiter.request_gap = request_gap

    return limiter


def with_retry(
    max_attempts: int = 5,
    backoff_seconds: Tuple[int, ...] = (2, 4, 8, 16, 32),
    retryable_errors: Tuple[Type[Exception], ...] = RETRYABLE_ERRORS,
):
    """
    带指数退避的重试装饰器
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # 尝试提取 output_path 以便在日志中显示上下文
            output_path = kwargs.get("output_path")
            # 如果是位置参数，generate_image 的 output_path 是第 5 个参数 (self, prompt, ref, ar, output_path)
            if not output_path and len(args) > 4:
                output_path = args[4]

            context_str = ""
            if output_path:
                context_str = f"[{Path(output_path).name}] "

            last_error = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    # Catch ALL exceptions and check if they look like a retryable error
                    last_error = e
                    should_retry = False

                    # Check if it's in our explicit list
                    if isinstance(e, retryable_errors):
                        should_retry = True

                    # Check by string analysis (catch-all for 429/500/503)
                    error_str = str(e)
                    if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                        should_retry = True
                    elif "500" in error_str or "InternalServerError" in error_str:
                        should_retry = True
                    elif "503" in error_str or "ServiceUnavailable" in error_str:
                        should_retry = True

                    if not should_retry:
                        raise e

                    if attempt < max_attempts - 1:
                        # 确保不超过 backoff 数组长度
                        backoff_idx = min(attempt, len(backoff_seconds) - 1)
                        base_wait = backoff_seconds[backoff_idx]
                        jitter = random.uniform(0, 2)  # 0-2秒随机抖动
                        wait_time = base_wait + jitter
                        logger.warning(
                            "%sAPI 调用异常: %s - %s",
                            context_str, type(e).__name__, str(e)[:200],
                        )
                        logger.warning(
                            "%s重试 %d/%d, %.1f 秒后...",
                            context_str, attempt + 1, max_attempts - 1, wait_time,
                        )
                        time.sleep(wait_time)
            raise last_error

        return wrapper

    return decorator


def with_retry_async(
    max_attempts: int = 5,
    backoff_seconds: Tuple[int, ...] = (2, 4, 8, 16, 32),
    retryable_errors: Tuple[Type[Exception], ...] = RETRYABLE_ERRORS,
):
    """
    异步函数重试装饰器，带指数退避和随机抖动
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # 尝试提取 output_path 以便在日志中显示上下文
            output_path = kwargs.get("output_path")
            if not output_path and len(args) > 4:
                output_path = args[4]

            context_str = ""
            if output_path:
                context_str = f"[{Path(output_path).name}] "

            last_error = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    should_retry = False

                    if isinstance(e, retryable_errors):
                        should_retry = True

                    error_str = str(e)
                    if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                        should_retry = True
                    elif "500" in error_str or "InternalServerError" in error_str:
                        should_retry = True
                    elif "503" in error_str or "ServiceUnavailable" in error_str:
                        should_retry = True

                    if not should_retry:
                        raise e

                    if attempt < max_attempts - 1:
                        backoff_idx = min(attempt, len(backoff_seconds) - 1)
                        base_wait = backoff_seconds[backoff_idx]
                        jitter = random.uniform(0, 2)  # 0-2秒随机抖动
                        wait_time = base_wait + jitter
                        logger.warning(
                            "%sAPI 调用异常: %s - %s",
                            context_str, type(e).__name__, str(e)[:200],
                        )
                        logger.warning(
                            "%s重试 %d/%d, %.1f 秒后...",
                            context_str, attempt + 1, max_attempts - 1, wait_time,
                        )
                        await asyncio.sleep(wait_time)
            raise last_error

        return wrapper

    return decorator


# 加载 .env 文件
try:
    from dotenv import load_dotenv

    # 从项目根目录加载 .env
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        # 也尝试从当前工作目录加载
        load_dotenv()
except ImportError:
    pass  # python-dotenv 未安装时跳过


class GeminiClient:
    """Gemini API 客户端封装（文本生成 + 风格分析）"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        rate_limiter: Optional[RateLimiter] = None,
        backend: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        gcs_bucket: Optional[str] = None,
        image_model: Optional[str] = None,
        video_model: Optional[str] = None,
    ):
        """
        初始化 Gemini 客户端

        支持两种后端：
        - AI Studio（默认）：使用 api_key
        - Vertex AI：使用 GCP 项目和服务账号凭据

        Args:
            api_key: API 密钥（仅 AI Studio 模式），默认从环境变量 GEMINI_API_KEY 读取
            rate_limiter: 可选的限流器实例
            backend: 后端类型（aistudio/vertex），默认 aistudio
            base_url: AI Studio 自定义 base URL（None 时从 GEMINI_BASE_URL 环境变量读取）
            gcs_bucket: Vertex AI GCS bucket（None 时从 VERTEX_GCS_BUCKET 环境变量读取）
            image_model: (已弃用，保留兼容性) 图片模型名称
            video_model: (已弃用，保留兼容性) 视频模型名称
        """
        from google import genai
        from google.genai import types

        self.types = types
        self.rate_limiter = rate_limiter or get_shared_rate_limiter()
        raw_backend = backend or "aistudio"
        self.backend = str(raw_backend).strip().lower() or "aistudio"
        self.credentials = None  # 用于 Vertex AI 模式
        self.project_id = None  # 用于 Vertex AI 模式
        self.gcs_bucket = None  # 用于 Vertex AI 模式的视频延长输出

        if self.backend == "vertex":
            # Vertex AI 模式（使用 JSON 服务账号凭证）
            import json as json_module

            from google.oauth2 import service_account

            from .system_config import resolve_vertex_credentials_path

            # 查找凭证文件（优先 vertex_credentials.json，兼容 vertex_keys/*.json）
            credentials_file = resolve_vertex_credentials_path(Path(__file__).parent.parent)
            if credentials_file is None:
                raise ValueError(
                    "未找到 Vertex AI 凭证文件\n"
                    "请将服务账号 JSON 文件放入 vertex_keys/ 目录"
                )

            # 从凭证文件读取项目 ID
            with open(credentials_file) as f:
                creds_data = json_module.load(f)
            self.project_id = creds_data.get("project_id")

            if not self.project_id:
                raise ValueError(f"凭证文件 {credentials_file} 中未找到 project_id")

            # 读取 GCS bucket 配置
            self.gcs_bucket = gcs_bucket

            # 加载服务账号凭证并添加必要的 scopes
            self.credentials = service_account.Credentials.from_service_account_file(
                str(credentials_file), scopes=VERTEX_SCOPES
            )

            self.client = genai.Client(
                vertexai=True,
                project=self.project_id,
                location="global",
                credentials=self.credentials,
            )
            logger.info("使用 Vertex AI 后端（凭证: %s）", credentials_file.name)
        else:
            # AI Studio 模式（默认）
            self.api_key = api_key
            if not self.api_key:
                raise ValueError(
                    "Gemini API Key 未提供。请在「全局设置 → 供应商」页面配置 API Key。"
                )

            effective_base_url = base_url
            http_options = {"base_url": effective_base_url} if effective_base_url else None
            self.client = genai.Client(api_key=self.api_key, http_options=http_options)
            if effective_base_url:
                logger.info("使用 AI Studio 后端（Base URL: %s）", effective_base_url)
            else:
                logger.info("使用 AI Studio 后端")

    @staticmethod
    def _load_image_detached(image_path: Union[str, Path]) -> Image.Image:
        """
        从路径加载图片并与底层文件句柄解绑。

        返回的 Image 对象驻留内存，不再持有打开的文件描述符。
        """
        with Image.open(image_path) as img:
            return img.copy()

    def _prepare_text_config(self, response_schema: Optional[Dict]) -> Optional[Dict]:
        """构建文本生成配置"""
        if response_schema:
            return {
                "response_mime_type": "application/json",
                "response_json_schema": response_schema,
            }
        return None

    def _process_text_response(self, response) -> str:
        """解析文本生成响应"""
        return response.text

    @with_retry(max_attempts=3, backoff_seconds=(2, 4, 8))
    def generate_text(
        self,
        prompt: str,
        model: str = "gemini-3-flash-preview",
        response_schema: Optional[Dict] = None,
    ) -> str:
        """
        生成文本内容，支持 Structured Outputs

        Args:
            prompt: 提示词
            model: 模型名称，默认使用 flash 模型
            response_schema: 可选的 JSON Schema，用于 Structured Outputs

        Returns:
            生成的文本内容
        """
        config = self._prepare_text_config(response_schema)
        response = self.client.models.generate_content(
            model=model,
            contents=prompt,
            config=config,
        )
        return self._process_text_response(response)

    @with_retry_async(max_attempts=3, backoff_seconds=(2, 4, 8))
    async def generate_text_async(
        self,
        prompt: str,
        model: str = "gemini-3-flash-preview",
        response_schema: Optional[Dict] = None,
    ) -> str:
        """
        异步生成文本内容，支持 Structured Outputs

        使用 genai 原生异步 API: client.aio.models.generate_content()

        Args:
            prompt: 提示词
            model: 模型名称，默认使用 flash 模型
            response_schema: 可选的 JSON Schema，用于 Structured Outputs

        Returns:
            生成的文本内容
        """
        config = self._prepare_text_config(response_schema)
        response = await self.client.aio.models.generate_content(
            model=model,
            contents=prompt,
            config=config,
        )
        return self._process_text_response(response)

    @with_retry(max_attempts=3, backoff_seconds=(2, 4, 8))
    def analyze_style_image(
        self,
        image: Union[str, Path, Image.Image],
        model: str = "gemini-3-flash-preview",
    ) -> str:
        """
        分析图片的视觉风格

        Args:
            image: 图片路径或 PIL Image 对象
            model: 模型名称，默认使用 flash 模型

        Returns:
            风格描述文字（逗号分隔的描述词列表）
        """
        close_after_use = False

        # 准备图片
        if isinstance(image, (str, Path)):
            img = self._load_image_detached(image)
            close_after_use = True
        else:
            img = image

        # 风格分析 Prompt（参考 Storycraft）
        prompt = (
            "Analyze the visual style of this image. Describe the lighting, "
            "color palette, medium (e.g., oil painting, digital art, photography), "
            "texture, and overall mood. Do NOT describe the subject matter "
            "(e.g., people, objects) or specific content. Focus ONLY on the "
            "artistic style. Provide a concise comma-separated list of descriptors "
            "suitable for an image generation prompt."
        )

        try:
            # 调用 API
            response = self.client.models.generate_content(
                model=model, contents=[img, prompt]
            )
            return response.text.strip()
        finally:
            if close_after_use:
                img.close()
