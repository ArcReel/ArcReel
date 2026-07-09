"""Gemini 文本生成后端。"""

from __future__ import annotations

import json
import logging
from dataclasses import replace

from google import genai
from PIL import Image
from pydantic import BaseModel, ValidationError

from ..config.url_utils import normalize_base_url
from ..gemini_shared import VERTEX_SCOPES, with_retry_async
from ..logging_utils import format_kwargs_for_log
from ..providers import PROVIDER_GEMINI
from ..text_utils import strip_json_code_fences
from .base import (
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
    check_truncation,
    is_valid_json,
    resolve_schema,
    structured_fallback_reason,
    summarize_validation_error,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3-flash-preview"

# prompt 注入降级的最大调用次数：首次注入 schema + 一次带校验错误反馈的重试。
# 每次都是计费调用；schema 注入后仍两连败的模型/中转，再多重试收益趋零。
_FALLBACK_MAX_ATTEMPTS = 2


# 这些关键字的值是「名字 → 子 schema 的映射」：其 key 是属性名/定义名，不是 schema 关键字。
# 递归进入时，每个值才是子 schema（key 可能恰好叫 ``const``，不可当关键字识别）。
_SUBSCHEMA_MAP_KEYS = frozenset({"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"})
# 这些关键字的值是「实例数据」而非子 schema：不可递归进去（里面恰好叫 ``const`` 的内容是数据）。
_INSTANCE_KEYWORDS = frozenset({"const", "enum", "default", "examples"})


def _const_to_enum(node: object, *, in_subschema_map: bool = False) -> object:
    """把 schema 里「值为标量」的 ``const: X`` 归一为 ``enum: [X]``（语义等价）。

    单值 ``Literal`` 在 ``model_json_schema()`` 里渲染为 ``const``，而 ``const`` 不在 Gemini
    ``response_json_schema`` 的受支持特性内（``enum`` 在）。归一后单值约束落到受支持的 ``enum``，
    保留生成层硬约束。

    ``const`` 出现的位置有三种，须区分对待（这是正确性的不可约最小状态机）：
    - **schema 关键字**：归一（仅标量，对齐本仓库唯一的 const 形态——单值时长 Literal）；
    - **字段名**（``_SUBSCHEMA_MAP_KEYS`` 映射的 key）：当前 dict 的 key 是名字，其值仍是子 schema，
      继续按 schema 递归（里面真正的 const 照常归一）；
    - **实例数据**（``_INSTANCE_KEYWORDS`` 的值）：原样保留、不递归。
    """
    if isinstance(node, list):
        return [_const_to_enum(item) for item in node]
    if not isinstance(node, dict):
        return node
    if in_subschema_map:
        # 当前 dict 的 key 是属性名/定义名；每个值才是子 schema
        return {k: _const_to_enum(v) for k, v in node.items()}
    out: dict = {}
    for k, v in node.items():
        if k in _INSTANCE_KEYWORDS:
            out[k] = v  # 值是实例数据，原样保留
        else:
            out[k] = _const_to_enum(v, in_subschema_map=k in _SUBSCHEMA_MAP_KEYS)
    if "const" in out and (out["const"] is None or isinstance(out["const"], (str, int, float, bool))):
        out["enum"] = [out.pop("const")]
    return out


def _to_response_json_schema(schema: dict | type) -> dict:
    """把 response_schema 统一转成 Gemini ``response_json_schema`` 可消费的 JSON Schema dict。

    Gemini 有两条结构化输出通道：``response_schema``（``types.Schema``，OpenAPI 子集，``enum``
    仅支持字符串）与 ``response_json_schema``（标准 JSON Schema，``enum`` 支持字符串与数字）。
    ``build_episode_script_model`` 把 ``duration_seconds`` 收紧为 ``Literal[*supported_durations]``
    的整数 enum，走前者会在 SDK schema 转换时抛 "Input should be a valid string"，故统一走后者：
    先 ``resolve_schema`` 内联 ``$ref``，再把单值 ``const`` 归一为 ``enum``。
    """
    normalized = _const_to_enum(resolve_schema(schema))
    assert isinstance(normalized, dict)  # resolve_schema 必返回 dict
    return normalized


class GeminiTextBackend:
    """Gemini 文本生成后端，支持 AI Studio 和 Vertex AI 两种模式。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        backend: str = "aistudio",
        base_url: str | None = None,
        gcs_bucket: str | None = None,
    ):
        self._model = model or DEFAULT_MODEL
        raw_backend = backend or "aistudio"
        self._backend = str(raw_backend).strip().lower() or "aistudio"

        if self._backend == "vertex":
            import json as json_module

            from google.oauth2 import service_account

            from ..system_config import resolve_vertex_credentials_path

            credentials_file = resolve_vertex_credentials_path()
            if credentials_file is None:
                raise ValueError("未找到 Vertex AI 凭证文件\n请将服务账号 JSON 文件放入 vertex_keys/ 目录")

            with open(credentials_file, encoding="utf-8") as f:
                creds_data = json_module.load(f)
            project_id = creds_data.get("project_id")

            if not project_id:
                raise ValueError(f"凭证文件 {credentials_file} 中未找到 project_id")

            credentials = service_account.Credentials.from_service_account_file(
                str(credentials_file), scopes=VERTEX_SCOPES
            )

            self._client = genai.Client(
                vertexai=True,
                project=project_id,
                location="global",
                credentials=credentials,
            )
            logger.info("GeminiTextBackend: 使用 Vertex AI 后端（凭证: %s）", credentials_file.name)
        else:
            if not api_key:
                raise ValueError("Gemini API Key 未提供（API Key is required for AI Studio mode）。")
            effective_base_url = normalize_base_url(base_url)
            http_options = {"base_url": effective_base_url} if effective_base_url else None
            self._client = genai.Client(api_key=api_key, http_options=http_options)  # type: ignore[arg-type]
            if base_url:
                logger.info("GeminiTextBackend: 使用 AI Studio 后端（Base URL: %s）", base_url)
            else:
                logger.info("GeminiTextBackend: 使用 AI Studio 后端")

    @property
    def name(self) -> str:
        return PROVIDER_GEMINI

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[TextCapability]:
        return {
            TextCapability.TEXT_GENERATION,
            TextCapability.STRUCTURED_OUTPUT,
            TextCapability.VISION,
        }

    def _build_config(
        self,
        response_schema: dict | type | None,
        system_prompt: str | None,
        max_output_tokens: int | None = None,
    ) -> dict:
        """构建 generate_content 的 config 字典。"""
        config: dict = {}
        if response_schema:
            config["response_mime_type"] = "application/json"
            config["response_json_schema"] = _to_response_json_schema(response_schema)
        if system_prompt:
            config["system_instruction"] = system_prompt
        if max_output_tokens is not None:
            config["max_output_tokens"] = max_output_tokens
        return config

    def _build_contents(self, request: TextGenerationRequest) -> list:
        """构建 contents 列表（图片 parts + 文本 prompt）。"""
        contents: list = []

        if request.images:
            for img_input in request.images:
                if img_input.path is not None:
                    pil_img = Image.open(img_input.path)
                    contents.append(pil_img)
                elif img_input.url is not None:
                    # URL 型图片直接作为字符串传递，SDK 内部会处理
                    contents.append(img_input.url)

        contents.append(request.prompt)
        return contents

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        """异步生成文本，支持结构化输出和 vision。

        本方法不带重试装饰器：瞬态错误重试在 :meth:`_generate_native` 层完成。若把复验/
        降级也包进重试范围，降级尝试的失败会连带重放已成功的原生调用（重试叠乘），且降级
        穷尽的 ValueError 消息含模型侧动态文本，可能误中重试判定的字符串模式。
        """
        native = await self._generate_native(request, structured=bool(request.response_schema))

        if request.response_schema:
            # 成功路径复验：HTTP 200 后校验返回是否真满足 schema。中转/代理可能静默忽略
            # response_json_schema 返回散文或违例 JSON，直接放行会一路漏到下游 Pydantic
            # 校验才抛裸 ValidationError。Gemini 原生请求无 strict 声明，复验用 strict=False，
            # 容忍可强转值，避免对供应商已接受的合法响应触发多余的计费降级调用。
            fallback_reason = structured_fallback_reason(native.text, request.response_schema, strict=False)
            if fallback_reason:
                logger.warning("原生 response_json_schema %s，降级到 prompt 注入路径", fallback_reason)
                result = await self._prompt_json_fallback(request)
                # 这次原生 200 调用已被计费，把它的 token 并入降级结果，否则会系统性漏记。
                # 仅在至少一侧有计量时相加；两侧皆 None（未追踪）保持 None，不塌成字面 0 token。
                if result.input_tokens is not None or native.input_tokens is not None:
                    result.input_tokens = (result.input_tokens or 0) + (native.input_tokens or 0)
                if result.output_tokens is not None or native.output_tokens is not None:
                    result.output_tokens = (result.output_tokens or 0) + (native.output_tokens or 0)
                return result

        return native

    @with_retry_async()
    async def _generate_native(self, request: TextGenerationRequest, *, structured: bool) -> TextGenerationResult:
        """单次原生 SDK 调用：构造 config/contents、发请求、截断处理与瞬态错误重试。

        ``structured`` 由调用方按本次 generate() 的原始请求诉求传入，与 ``request.response_schema``
        本身解耦——:meth:`_prompt_json_fallback` 会把 ``response_schema`` 置空后仍复用本方法
        （schema 已改写进 prompt），但那仍是一次结构化输出尝试，截断同样要硬错误而非仅告警。
        """
        config = self._build_config(
            request.response_schema,
            request.system_prompt,
            request.max_output_tokens,
        )
        contents = self._build_contents(request)

        logger.info(
            "调用 %s 文本 SDK payload=%s",
            self.name,
            format_kwargs_for_log({"model": self._model, "contents": contents, "config": config or None}),
        )
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=contents,
            config=config if config else None,  # type: ignore[arg-type]
        )

        text = response.text.strip() if response.text else ""

        input_tokens: int | None = None
        output_tokens: int | None = None
        if response.usage_metadata is not None:
            input_tokens = getattr(response.usage_metadata, "prompt_token_count", None)
            output_tokens = getattr(response.usage_metadata, "candidates_token_count", None)

        candidates = getattr(response, "candidates", None) or []
        if candidates:
            finish_reason = getattr(candidates[0], "finish_reason", None)
            # Gemini finish_reason 可能是枚举对象，转 str 后再比对
            check_truncation(
                str(finish_reason).rsplit(".", 1)[-1] if finish_reason is not None else None,
                provider=PROVIDER_GEMINI,
                model=self._model,
                output_tokens=output_tokens,
                structured=structured,
            )

        return TextGenerationResult(
            text=text,
            provider=PROVIDER_GEMINI,
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    async def _prompt_json_fallback(self, request: TextGenerationRequest) -> TextGenerationResult:
        """Prompt 注入降级：schema 写进 prompt 重发纯文本调用，剥栅栏后校验，失败带反馈重试。

        触发场景是中转/代理静默忽略 wire 级结构化参数（response_json_schema），此时任何
        wire 级通道（含 instructor genai 集成的 JSON/TOOLS 模式，同样落在 response_schema /
        function calling 参数上）都会被同一中转忽略，把 schema 约束写进 prompt 是唯一不依赖
        wire 参数的手段。校验通过后返回规范化 JSON（model_dump_json），下游解析必定成功。
        """
        assert request.response_schema is not None  # 调用方（generate 复验分支）保证
        schema_dict = resolve_schema(request.response_schema)
        schema_model = request.response_schema if isinstance(request.response_schema, type) else None

        base_prompt = (
            f"{request.prompt}\n\n"
            "仅输出一个符合以下 JSON Schema 的 JSON 对象，"
            "不要输出任何解释、前后缀或 markdown 代码栅栏：\n"
            f"{json.dumps(schema_dict, ensure_ascii=False)}"
        )
        feedback = ""
        total_input: int | None = None
        total_output: int | None = None
        last_reason = ""
        for _attempt in range(_FALLBACK_MAX_ATTEMPTS):
            fb_request = replace(request, prompt=base_prompt + feedback, response_schema=None)
            # 直调 _generate_native 复用日志、截断处理与瞬态错误重试；不经 generate 的
            # 复验分支，每次降级尝试只产生自己这一层的重试，不与外层调用叠乘。structured
            # 固定 True：response_schema 虽已置空改走 prompt 注入，这仍是一次结构化输出尝试。
            fb_result = await self._generate_native(fb_request, structured=True)
            if fb_result.input_tokens is not None or total_input is not None:
                total_input = (total_input or 0) + (fb_result.input_tokens or 0)
            if fb_result.output_tokens is not None or total_output is not None:
                total_output = (total_output or 0) + (fb_result.output_tokens or 0)

            text = strip_json_code_fences(fb_result.text)
            normalized, last_reason = _validate_fallback_text(text, schema_model)
            if normalized is not None:
                return TextGenerationResult(
                    text=normalized,
                    provider=PROVIDER_GEMINI,
                    model=self._model,
                    input_tokens=total_input,
                    output_tokens=total_output,
                )
            feedback = f"\n\n上次输出不符合要求（{last_reason}），请严格按上述 JSON Schema 重新输出纯 JSON。"
            logger.warning("prompt 注入降级输出校验未通过（%s），带反馈重试", last_reason)

        raise ValueError(
            f"模型 {_FALLBACK_MAX_ATTEMPTS + 1} 次尝试后仍未返回符合要求的 JSON（{last_reason}）；"
            "当前供应商或模型可能不支持结构化输出，请更换模型或供应商后重试"
        )


def _validate_fallback_text(text: str, schema_model: type | None) -> tuple[str | None, str]:
    """校验降级输出并规范化。返回 (规范化 JSON 文本, "") 或 (None, 失败原因)。

    Pydantic 模型走完整 schema 校验并以 model_dump_json 规范化；dict schema 无对应模型，
    沿用「合法 JSON 即放行」口径（与 structured_fallback_reason 对 dict schema 的行为对齐）。
    """
    if schema_model is not None and issubclass(schema_model, BaseModel):
        try:
            return schema_model.model_validate_json(text).model_dump_json(), ""
        except ValidationError as exc:
            return None, summarize_validation_error(exc)
    if is_valid_json(text):
        return text, ""
    return None, "输出不是合法 JSON"
