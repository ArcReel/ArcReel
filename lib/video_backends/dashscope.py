"""DashScopeVideoBackend — 阿里百炼 HappyHorse / 万相视频生成后端（异步两步式）。

走原生 video-generation/video-synthesis 异步端点：submit 取 task_id → 轮询
GET /tasks/{id} 至 SUCCEEDED → 下载 video_url。覆盖 happyhorse-1.0 / happyhorse-1.1
与 wan2.7 系列的 t2v / i2v / r2v，以及单模型通吃三条路径的 wan3.0。

schema 的确权程度按型号分两档：happyhorse 与 wan2.7 依据 docs/dashscope-docs/ 一手
核实快照；wan3.0 无对应快照，其请求形态按 2.7 形状类推，出处与类推范围见 _WAN3_*
常量处的说明。

注：t2v/i2v 起始帧用 media[{type:"first_frame"}]（first_frame type 在 r2v media
枚举中确权）；尾帧 / 续写字段在一手 docs 未确权，故 happyhorse 与 wan2.7 的 i2v 仅
声明首帧能力，不臆造。wan3.0 的尾帧是上述类推档的一部分，不受这条约束。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

from lib.dashscope_shared import (
    DASHSCOPE_POLL_INTERVAL_SECONDS,
    dashscope_failure_reason,
    dashscope_headers,
    dashscope_native_base_url,
    extract_billing_duration,
    extract_task_id,
    extract_video_url,
    image_to_data_uri,
    is_dashscope_expired,
    is_dashscope_terminal,
    resolve_dashscope_api_key,
    safe_body_for_log,
)
from lib.data_uri import file_to_data_uri
from lib.logging_utils import format_kwargs_for_log
from lib.providers import PROVIDER_DASHSCOPE
from lib.retry import (
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DOWNLOAD_BACKOFF_SECONDS,
    DOWNLOAD_MAX_ATTEMPTS,
    with_retry_async,
)
from lib.video_backends.base import (
    ProviderJobIdPersistenceMixin,
    ReferenceAudioMode,
    ResumeExpiredError,
    VideoCapabilities,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
    poll_with_retry,
    should_retry_download,
    should_retry_poll,
    should_retry_submit,
    submit_post,
)

logger = logging.getLogger(__name__)


def _read_image_or_none(path: Path) -> str | None:
    """读成 data URI；缺失（目录/非常规文件，含空串解析出的 "."）或 IO 失败（权限/并发删除）返回 None。"""
    if not path.is_file():
        return None
    try:
        return image_to_data_uri(path)
    except OSError as exc:
        logger.warning("DashScope 图片读取失败: %s (%s)", path, exc)
        return None


# wan2.7 的 reference_voice 接受 wav / mp3（官方《万相2.7-参考生视频》reference_voice 章节），
# URL 形态与 media.url 同为 http / oss / base64 data URI。
_REFERENCE_AUDIO_MIME_TYPES = {".wav": "audio/wav", ".mp3": "audio/mpeg"}


def _read_reference_audio_or_none(path: Path) -> str | None:
    """参考音频 → base64 data URI；文件缺失或 IO 失败返回 None（格式另由调用方先行拒绝）。"""
    mime = _REFERENCE_AUDIO_MIME_TYPES[path.suffix.lower()]
    if not path.is_file():
        return None
    try:
        return file_to_data_uri(path, mime)
    except OSError as exc:
        logger.warning("DashScope 参考音频读取失败: %s (%s)", path, exc)
        return None


DEFAULT_MODEL = "happyhorse-1.1-i2v"

_VIDEO_ENDPOINT = "/services/aigc/video-generation/video-synthesis"

_MIN_POLL_TIMEOUT_SECONDS = 900.0
_POLL_TIMEOUT_PER_SECOND = 60.0

# wan2.7-r2v 的 reference_voice 逐段挂在参考素材项上，故音频段数上限等同参考素材总数上限
# （官方：参考图像 + 参考视频 ≤ 5）。
_WAN27_R2V_MAX_REFERENCE = 5

# wan2.7 全家族 prompt 上限 5000 字符（官方参数表原文「超过部分会自动截断」，错误码表无对应
# 条目——超限不报错、直接静默截断并照常计费，故由 gate_video_request 在付费前拒绝）。
_WAN27_MAX_PROMPT_CHARS = 5000

# wan3.0 单模型覆盖文生/图生/参考生三条路径：首帧 + 尾帧，参考图 10 张，参考音频 5 段、
# 总时长 15 秒，prompt 上限 20000 字符。prompt 超限与 2.7 同为静默截断且照常计费，同样由
# gate_video_request 前置拒绝。
# 出处：万相 3.0 发布说明所列能力上限。与其余型号不同，wan3.0 没有可引的一手 API schema——
# 下方 media 条目类型（last_frame / reference_audio）与 parameters["audio"] 的字面量均按 2.7
# 形状类推，对端如报参数错误应以此处为首查点。
_WAN3_MAX_REFERENCE_IMAGES = 10
_WAN3_MAX_REFERENCE_AUDIO = 5
_WAN3_MAX_REFERENCE_AUDIO_TOTAL_SECONDS = 15.0
_WAN3_MAX_PROMPT_CHARS = 20000
_WAN3_MODEL_KEY = "wan3.0-video"

# 万相 3.0 家族 model_id 识别（连字符/下划线可选、不锚版本号）：此处是本后端的请求形态分派，
# 也是 lib.custom_provider.duration_presets（时长档位推断）与 endpoints.py（端点路由推断）共用
# 的唯一正则来源——三处须按同一匹配宽度判 wan3，否则会出现某个 model_id 被路由到本后端、却因
# 本后端认不出它是 wan3 而退回通用档案（丢参考图/尾帧/音轨参数）的矛盾。版本前缀与模态 token
# （下方 WAN_IMAGE_TO_VIDEO_PATTERN）须接受相同的分隔符集合，避免同一规则组内宽容度不对称。
# 两侧标识符边界要求非字母数字，避免匹配到 "swan3"、"vendorwan3" 这类含 wan3 子串但并非该家族
# 的第三方型号名。
WAN3_PATTERN = re.compile(r"(?<![a-z0-9])wan[-_]?3(?![a-z0-9])", re.I)

# 万相 2.7 家族 model_id 识别（连字符/下划线可选、标识符边界避免误吞 "swan2"、"wan20"）：
# 使连字符形态（"wan-2.7"）与点号形态（"wan2.7"）在图像/视频归属与端点路由上得出一致结论——
# 这是本正则唯一确权的范围。
#
# 只锚 2.7、不覆盖其余 2.x 小版本：本后端固定请求
# `/services/aigc/video-generation/video-synthesis`（_VIDEO_ENDPOINT），而 wan2.1 / wan2.2-s2v
# 按 docs/research/arcreel-video-api-protocol-research.md 记录走的是旧端点
# `/services/aigc/image2video/video-synthesis/`、payload 字段也不同（如 wan2.6 用 `size` 而非
# 2.7 的 `resolution`+`ratio`），并入本正则会把协议不兼容的请求送到这个端点。
# 点号形态（如 "wan2.1-kf2v"）不受本正则约束，归 WAN_DOT_FORM_PATTERN 判定，其路由是否也应
# 收窄到 2.7 需要供应商 API 事实与产品判断，不由本正则的匹配宽度代为决定。
WAN2_PATTERN = re.compile(r"(?<![a-z0-9])wan[-_]?2\.7(?![a-z0-9])", re.I)

# 点号形态万相 2.x（2.7 以外，如 "wan2.1-kf2v"）的家族判定：标识符边界要求非字母数字，避免
# "swan2.7-r2v"、"vendorwan2.7-t2v" 这类含 "wan2." 子串但并非该家族的第三方型号名被误判。
WAN_DOT_FORM_PATTERN = re.compile(r"(?<![a-z0-9])wan2\.\d+(?![a-z0-9])", re.I)

# happyhorse 家族判定：标识符边界要求非字母数字，避免 "myhappyhorse-1.0-r2v" / "happyhorsefoo"
# 这类第三方型号名被字面子串误吞。
HAPPYHORSE_PATTERN = re.compile(r"(?<![a-z0-9])happyhorse(?![a-z0-9])", re.I)

# wan 家族 image-to-video 续接语法（"wan-2.7-image-to-video" / "wan_2.7-image2video" /
# "wan-3-turbo-image-to-video"）：只识别 "image-(to|2)-video" 这一种确定拼写，不识别 img2vid/i2v
# 等其他缩写。wan2.7 归一化用它把该后缀折成 "i2v" 再查 _MODEL_PROFILES；wan3/wan2x_dot 家族用它
# 区分图像/视频归属（wan3.0-video-image 等真图像别名不含该语法，不受影响）。两侧标识符边界避免
# "wan2.7-fooimage-to-video" / "wan2.7-image-to-videofoo" 这类相邻字母被误判命中。
WAN_IMAGE_TO_VIDEO_PATTERN = re.compile(r"(?<![a-z0-9])image[-_]?(?:to|2)[-_]?video(?![a-z0-9])", re.I)

# wan2.7-videoedit（指令式视频编辑，见 docs/research/arcreel-vendor-integration-research.md）是
# 万相家族内真实存在的独立模态，但本后端只实现了 t2v/i2v/r2v 三档的请求构造，没有该模态所需的
# 输入视频传输字段。命中家族正则但落这个模态的 id 须排除出原生路由与已知能力档，否则会带着
# _DEFAULT_PROFILE（丢失该模态实际所需的能力声明）发出本后端无法正确构造的请求。
WAN_VIDEOEDIT_PATTERN = re.compile(r"video[-_]?edit", re.I)

WanFamily = Literal["happyhorse", "wan2.7", "wan3", "wan2x_dot"]


@dataclass(frozen=True)
class WanClassification:
    """model_id 在万相/happyhorse 家族判定链上的结构化结论。

    唯一判定入口——家族归属、分隔符归一化、标识符边界、image-to-video 续接语法、未实现模态排除
    均只在 `classify_wan_model` 里实现一次。端点路由（endpoints.py::infer_endpoint）、
    能力档（本模块 `_profile_for_model`）、时长档（duration_presets.py）三处判定点都只消费这份
    结论，不得再各自对 model_id 做正则匹配，避免同一家族的边界规则在多处漂移出互斥组合（例如
    命中原生路由却拿到与该路由不兼容的能力档）。
    """

    family: WanFamily | None
    is_image_to_video: bool  # wan 系列 image-to-video 续接别名（本质视频，非图像变体）
    is_videoedit: bool  # wan2.7-videoedit：本后端未实现请求构造的模态
    profile_key: str | None  # 可直接查 _MODEL_PROFILES 的归一化 key；无法确定具体模态时为 None
    # wan2.7 家族是否解析出本后端已实现请求构造的具体模态（t2v/i2v/r2v）。wan2.7-s2v /
    # wan2.7-v2v / wan2.7-videoedit 等命中家族正则但模态未实现，须与已实现模态区分对待——两者混同
    # 会让请求体缺字段的模态被静默送去本后端无法正确处理的端点。wan3/happyhorse/wan2x_dot 恒为
    # True：wan3 单模型覆盖全部模态、happyhorse 与 wan2x_dot 的模态收窄不由本字段判定（前者未见
    # 需要排除的模态，后者见 profile_key 处的说明）。
    has_known_modality: bool


def classify_wan_model(model_id: str | None) -> WanClassification:
    """对 model_id 做一次判定，供路由/能力档/时长档复用同一结论。"""
    normalized = (model_id or "").strip().lower()
    if HAPPYHORSE_PATTERN.search(normalized):
        # happyhorse 无 image-to-video 续接语法与 videoedit 模态；t2v/i2v/r2v 具体档位交由
        # _profile_for_model 末尾的兜底子串匹配解析，此处不预先归一化。
        return WanClassification(
            family="happyhorse", is_image_to_video=False, is_videoedit=False, profile_key=None, has_known_modality=True
        )
    if WAN3_PATTERN.search(normalized):
        family: WanFamily = "wan3"
    elif WAN2_PATTERN.search(normalized):
        family = "wan2.7"
    elif WAN_DOT_FORM_PATTERN.search(normalized):
        family = "wan2x_dot"
    else:
        return WanClassification(
            family=None, is_image_to_video=False, is_videoedit=False, profile_key=None, has_known_modality=True
        )

    # videoedit 是 wan2.7 家族内独有的模态（见 WAN_VIDEOEDIT_PATTERN 处的说明）；wan3/wan2x_dot
    # 不受它约束，否则形如 "proxy-videoedit/wan3-turbo" 这类与 videoedit 无关的装饰前缀会被误吞，
    # 把本应走原生路由的 wan3 模型错误排除出去。
    is_videoedit = family == "wan2.7" and bool(WAN_VIDEOEDIT_PATTERN.search(normalized))
    is_image_to_video = bool(WAN_IMAGE_TO_VIDEO_PATTERN.search(normalized))
    profile_key: str | None = None
    has_known_modality = True
    if family == "wan3":
        profile_key = _WAN3_MODEL_KEY
    elif family == "wan2.7":
        profile_key = _normalize_wan27_alias(normalized)
        # wan2.7 的 payload 构造只实现了 t2v/i2v/r2v；videoedit 已由 is_videoedit 单独标记，
        # s2v/v2v 等其余未实现模态同样不能落原生路由。按标识符边界匹配 _MODEL_PROFILES 里的
        # wan2.7 已知 key（而非要求 profile_key 与某个 key 完全相等）：profile_key 保留了代理
        # 中转的装饰前后缀（"proxy/wan2.7-r2v"），精确相等会把这些合法装饰名也判成未知模态。
        has_known_modality = (
            _find_known_profile_key(profile_key, (k for k in _MODEL_PROFILES if k.startswith("wan2.7-"))) is not None
        )
    # wan2x_dot 无法从 model_id 直接归一化出确切 t2v/i2v/r2v 档位（其命名形态未收敛），
    # profile_key 留空，交由 _profile_for_model 末尾的兜底子串匹配处理（多数落 _DEFAULT_PROFILE）；
    # 是否收窄同 wan2.7 一样按已知模态门控需要供应商 API 事实与产品判断，不由本字段代为决定。
    return WanClassification(
        family=family,
        is_image_to_video=is_image_to_video,
        is_videoedit=is_videoedit,
        profile_key=profile_key,
        has_known_modality=has_known_modality,
    )


def _normalize_wan27_alias(normalized: str) -> str:
    """把 WAN2_PATTERN 命中的 wan2.7 别名折成 _MODEL_PROFILES key 固定使用的形态：
    "wan[-_]?2.7[-_]<modality>"，无论 wan/版本号/模态三段各自用哪种分隔符，统一成
    "wan2.7-<modality>"（modality 含 image-to-video 续接语法时进一步折成 "i2v"）。

    分两步是因为两段分隔符独立可变（"wan_2.7-r2v" 只有前段是下划线、"wan-2.7_r2v" 只有后段
    是下划线、"wan_2.7_r2v" 两段都是），任一段漏归一化都会导致结果不与 _MODEL_PROFILES 的 key
    构成子串关系，静默落 _DEFAULT_PROFILE。调用方须先用 WAN2_PATTERN.search 确认命中。
    """
    normalized = WAN2_PATTERN.sub("wan2.7", normalized)
    normalized = re.sub(r"wan2\.7_", "wan2.7-", normalized)
    return WAN_IMAGE_TO_VIDEO_PATTERN.sub("i2v", normalized)


# 按 model id 派发能力声明。happyhorse-r2v 仅 reference_image（无 first_frame）；
# wan2.7-r2v 额外支持首帧与参考音色。
_MODEL_PROFILES: dict[str, VideoCapabilities] = {
    "happyhorse-1.1-t2v": VideoCapabilities(first_frame=False),
    "happyhorse-1.1-i2v": VideoCapabilities(first_frame=True),
    "happyhorse-1.1-r2v": VideoCapabilities(first_frame=False, max_reference_images=9),
    "happyhorse-1.0-t2v": VideoCapabilities(first_frame=False),
    "happyhorse-1.0-i2v": VideoCapabilities(first_frame=True),
    "happyhorse-1.0-r2v": VideoCapabilities(first_frame=False, max_reference_images=9),
    "wan2.7-t2v": VideoCapabilities(first_frame=False, max_prompt_chars=_WAN27_MAX_PROMPT_CHARS),
    "wan2.7-i2v": VideoCapabilities(first_frame=True, max_prompt_chars=_WAN27_MAX_PROMPT_CHARS),
    # 带首帧的参考生视频是 wan2.7-r2v 的官方形态（_build_media 同请求组装
    # first_frame + reference_image）。
    "wan2.7-r2v": VideoCapabilities(
        first_frame=True,
        max_reference_images=_WAN27_R2V_MAX_REFERENCE,
        reference_audio_mode=ReferenceAudioMode.DIRECT,
        max_reference_audio_count=_WAN27_R2V_MAX_REFERENCE,
        # 音色挂在具体参考素材项上（_attach_reference_voices），不是独立的音色输入通道，
        # 编排层必须显式给出「谁的声音配哪张图」的映射，不能假设与 reference_audio_files 同序。
        reference_audio_per_image=True,
        max_prompt_chars=_WAN27_MAX_PROMPT_CHARS,
    ),
    # wan3.0 的参考音频是 media 数组里的独立条目（不像 2.7 挂在参考素材项上），故不声明
    # reference_audio_per_image，改由 max_reference_audio_total_seconds 约束总量。
    _WAN3_MODEL_KEY: VideoCapabilities(
        first_frame=True,
        last_frame=True,
        max_reference_images=_WAN3_MAX_REFERENCE_IMAGES,
        reference_audio_mode=ReferenceAudioMode.DIRECT,
        max_reference_audio_count=_WAN3_MAX_REFERENCE_AUDIO,
        max_reference_audio_total_seconds=_WAN3_MAX_REFERENCE_AUDIO_TOTAL_SECONDS,
        max_prompt_chars=_WAN3_MAX_PROMPT_CHARS,
    ),
}

# 未知 model（如代理中转自定义命名）按通用 i2v/t2v 处理，VideoCapabilities() 默认支持首帧。
_DEFAULT_PROFILE = VideoCapabilities()


def _is_wan3(model: str | None) -> bool:
    """识别 wan3.0 系列：它与其余型号在请求形态上有三处结构差异。

    一是单模型通吃文生/图生/参考生，参考图缺席是合法请求（其余带参考能力的型号都是 r2v
    专用，无参考图即无输入）；二是音轨由请求参数控制而非恒开；三是可走独立 maas 域名。
    三处都按型号名分派，profile 表只承载 VideoCapabilities 声明。
    """
    return bool(WAN3_PATTERN.search((model or "").strip().lower()))


def _find_known_profile_key(normalized: str, keys: Iterable[str]) -> str | None:
    """在 normalized 里按标识符边界查找 keys 中出现的第一个已知 key，均未命中则 None。

    两侧边界要求非字母数字：左侧避免 "swan2.7-r2v"（"s" 紧贴 "wan2.7-r2v"）、
    "myhappyhorse-1.0-r2v" 这类第三方型号名被字面子串误吞；右侧避免 "wan2.7-i2vfoo"、
    "happyhorse-1.0-r2vfoo" 这类未知变体后缀被截断误判成已知 key。代理中转的装饰前缀/后缀
    （"proxy/xxx"、"xxx-0715"）靠非字母数字分隔符天然满足两侧边界，不受影响。
    """
    for key in keys:
        if re.search(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])", normalized):
            return key
    return None


def _profile_for_model(model: str | None) -> VideoCapabilities:
    """按 model_id 解析能力档：先精确命中，再容忍代理中转的前后缀装饰。

    infer_endpoint 用 classify_wan_model 的同一结论路由到 dashscope-async-video，故此处也须
    子串容忍，否则 "proxy/happyhorse-1.0-r2v" / "wan2.7-r2v-0715" 这类装饰名会退回 _DEFAULT_PROFILE、
    丢掉 r2v 的 max_reference_images，_build_media 据此构造出错误 payload。
    仅带系列名而无变体后缀（如裸 "happyhorse"）无法判别 t2v/i2v/r2v，按设计回落通用默认。
    __init__ 与 video_capabilities_for_model 共用本函数，保持单一真相源。
    """
    normalized = (model or "").strip().lower()
    if not normalized:
        return _DEFAULT_PROFILE
    if normalized in _MODEL_PROFILES:
        return _MODEL_PROFILES[normalized]
    classification = classify_wan_model(normalized)
    if classification.profile_key is not None:
        normalized = classification.profile_key
    # 各 profile key（happyhorse-{1.0,1.1}-{t2v,i2v,r2v} / wan2.7-{t2v,i2v,r2v} /
    # wan3.0-video）互不为子串，无歧义，_find_known_profile_key 的边界匹配可安全逐一试探。
    known = _find_known_profile_key(normalized, _MODEL_PROFILES)
    if known is not None:
        return _MODEL_PROFILES[known]
    return _DEFAULT_PROFILE


class DashScopeVideoBackend(ProviderJobIdPersistenceMixin):
    """阿里百炼视频后端（异步 video-synthesis 端点）。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        wan3_base_url: str | None = None,
        http_timeout: float = 60.0,
    ) -> None:
        self._api_key = resolve_dashscope_api_key(api_key)
        self._base_url = dashscope_native_base_url(base_url)
        # wan3.0 专用 maas 域名含地域与 workspace，推不出也归一化不了，故按用户填写的
        # 完整 URL 原样使用（仅去掉尾部斜杠），未填则回落通用域名、由对端如实报错。
        self._wan3_base_url = (wan3_base_url or "").strip().rstrip("/") or None
        self._model = model or DEFAULT_MODEL
        self._http_timeout = http_timeout
        self._video_capabilities = _profile_for_model(self._model)

    @property
    def name(self) -> str:
        return PROVIDER_DASHSCOPE

    @property
    def model(self) -> str:
        return self._model

    @staticmethod
    def video_capabilities_for_model(model: str) -> VideoCapabilities:
        """按 model_id 纯计算参考图等 caps —— 不构造 SDK client（无需 api_key）。

        resolver 解析参考图上限时调本方法即可，不必构造整个 backend；instance property 委托至此，
        保持 backend 为单一真相源。
        """
        return _profile_for_model(model)

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

    @property
    def _request_base_url(self) -> str:
        """本型号请求实际走的域名。

        提交与轮询共用同一个：任务 id 只在创建它的 endpoint 上可查，两者分家会让 wan3.0
        任务提交成功后轮询到 404。
        """
        if self._wan3_base_url and _is_wan3(self._model):
            return self._wan3_base_url
        return self._base_url

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        payload = self._build_payload(request)
        logger.info(
            "调用 %s 视频 API model=%s body=%s",
            self.name,
            self._model,
            format_kwargs_for_log(safe_body_for_log(payload)),
        )
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            task_id = await self._create_task(client, payload)
            logger.info("DashScope 视频任务已创建: task_id=%s model=%s", task_id, self._model)
            # 一并写回实际提交域名（wan3.0 走独立 maas 域名，且两者都随用户配置可变）：
            # 续跑据此回放原域名，不然改配置后轮询会打到查不到该任务的主机。
            await self._persist_provider_job_id(
                request, task_id, provider=PROVIDER_DASHSCOPE, endpoint=self._request_base_url
            )
            return await self._poll_and_build(client, task_id, request, is_resume=False)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续已 submit 的 DashScope task：仅 poll + 下载（ADR 0007）。"""
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            return await self._poll_and_build(client, job_id, request, is_resume=True)

    # ── request building ────────────────────────────────────────────────

    def _build_payload(self, request: VideoGenerationRequest) -> dict:
        media = self._build_media(request)
        input_block: dict = {"prompt": request.prompt}
        if media:
            input_block["media"] = media

        parameters: dict = {
            "resolution": (request.resolution or "720p").upper(),
            "duration": request.duration_seconds,
            # HappyHorse 默认带 "Happy Horse" 水印，显式关闭
            "watermark": False,
        }
        # ratio 仅在无首帧时下传：图生视频/带首帧的参考生视频按首帧定宽高比，上游会忽略 ratio
        # （wan2.7「传 first_frame 时自动忽略」），HappyHorse 图生视频更直接把 ratio 当非法参数拒绝。
        # 默认 aspect_ratio 非空，若不门控会让带首帧的请求被上游拒。首帧缺席（文生视频/无首帧参考）才需 ratio。
        has_first_frame = any(m.get("type") == "first_frame" for m in media)
        if request.aspect_ratio and not has_first_frame:
            parameters["ratio"] = request.aspect_ratio
        if request.seed is not None:
            parameters["seed"] = request.seed
        # 音轨开关只对 wan3.0 下发：其余型号恒有声（registry 侧 audio_always_on），下发该参数
        # 会被上游当非法参数拒绝。开关可控与否的真相源在 registry，此处按型号分派运输形态。
        if _is_wan3(self._model):
            parameters["audio"] = request.generate_audio

        return {
            "model": self._model,
            "input": input_block,
            "parameters": parameters,
        }

    def _build_media(self, request: VideoGenerationRequest) -> list[dict]:
        caps = self._video_capabilities
        media: list[dict] = []
        if caps.first_frame and request.start_image:
            p = Path(request.start_image)
            # fail-loud：声明了首帧图却缺失（目录/非常规文件，含空串解析出的 "."）或读取失败即中止，
            # 不静默忽略 —— 否则用户拿到一个没用上首帧的结果却不知情。
            uri = _read_image_or_none(p)
            if uri is None:
                raise VideoCapabilityError("video_start_image_unreadable", model=self._model, name=p.name)
            media.append({"type": "first_frame", "url": uri})
        if caps.last_frame and request.end_image:
            p = Path(request.end_image)
            uri = _read_image_or_none(p)
            # 与首帧同为 fail-loud：声明了尾帧却读不到就中止，不静默产出一个没用上尾帧的结果。
            if uri is None:
                raise VideoCapabilityError("video_end_image_unreadable", model=self._model, name=p.name)
            media.append({"type": "last_frame", "url": uri})
        reference_items: list[dict] = []
        if caps.max_reference_images > 0:
            # r2v 必须有参考图。fail-loud：未提供 → required；任一声明的参考图缺失/不可读（is_file 不过
            # 或 read_bytes 抛 OSError）→ 报错列出文件名中止。不静默退化为无参考/子集生成（会产出错误
            # 结果且照常计费），让用户感知到有图未被使用。
            provided = [r for r in (request.reference_images or []) if r]
            if not provided and not _is_wan3(self._model):
                raise VideoCapabilityError("video_reference_images_required", model=self._model)
            data_uris: list[str] = []
            unreadable: list[str] = []
            for r in provided:
                p = Path(r)
                uri = _read_image_or_none(p)
                if uri is None:
                    unreadable.append(p.name)
                else:
                    data_uris.append(uri)
            if unreadable:
                raise VideoCapabilityError(
                    "video_reference_images_unreadable", model=self._model, names=", ".join(unreadable)
                )
            limit = caps.max_reference_images
            if len(data_uris) > limit:
                logger.warning(
                    "DashScope 参考图数量 %d 超过 model=%s 上限 %d，截断",
                    len(data_uris),
                    self._model,
                    limit,
                )
                data_uris = data_uris[:limit]
            reference_items = [{"type": "reference_image", "url": uri} for uri in data_uris]
        # 音频判定在参考素材循环之外：无参考素材可挂时也要走一遍，否则 wan2.7-i2v 这类无参考图
        # 能力的 model 收到音频会静默丢弃、照常扣费。自定义供应商可把 endpoint 级的
        # reference_audio_mode 覆盖成 direct，而 delegate 的 model profile 仍是真相源，故这条
        # 路径实际可达。两种挂载形态：per_image 的挂在参考素材项上（wan2.7-r2v），其余走 media
        # 数组里的独立 reference_audio 条目（wan3.0）。
        standalone_audio_items: list[dict] = []
        if caps.reference_audio_per_image:
            self._attach_reference_voices(reference_items, request)
        else:
            standalone_audio_items = self._build_reference_audio_items(request)
        media.extend(reference_items)
        media.extend(standalone_audio_items)
        return media

    def _build_reference_audio_items(self, request: VideoGenerationRequest) -> list[dict]:
        """把参考音频转成 media 数组里的独立 ``reference_audio`` 条目（wan3.0 形态）。

        与逐段挂载形态的差别只在落点：这里每段音频自成一个 media 条目，不占参考素材槽位，
        因而没有 slots 对齐一说。解码与 fail-loud 口径见 :meth:`_decode_reference_audio_uris`。
        """
        return [{"type": "reference_audio", "url": uri} for uri in self._decode_reference_audio_uris(request)]

    def _decode_reference_audio_uris(self, request: VideoGenerationRequest) -> list[str]:
        """把请求里的参考音频逐段读成 data URI，顺序与入参一一对应。

        两种挂载形态（独立 media 条目与逐段 ``reference_voice``）共用本方法，解码口径因而只有
        一处：能力档不支持音频、扩展名不在受支持格式内、任一段读不出来，都在此 fail-loud。
        不跳过任何一段——顺序即 prompt 中「音频N」的指认契约，静默少发一段会让该角色的音色
        声明无声失效且照常计费。段数与总时长上限由 ``gate_video_request`` 在付费前校验。
        """
        audio_files = list(request.reference_audio_files or [])
        if not audio_files:
            return []
        if self._video_capabilities.reference_audio_mode == ReferenceAudioMode.NONE:
            raise VideoCapabilityError("video_reference_audio_unsupported", provider=self.name, model=self._model)
        uris: list[str] = []
        unreadable: list[str] = []
        for audio in audio_files:
            path = Path(audio)
            if path.suffix.lower() not in _REFERENCE_AUDIO_MIME_TYPES:
                raise VideoCapabilityError(
                    "video_reference_audio_format_unsupported",
                    name=path.name,
                    supported=", ".join(sorted(_REFERENCE_AUDIO_MIME_TYPES)),
                )
            uri = _read_reference_audio_or_none(path)
            if uri is None:
                unreadable.append(path.name)
            else:
                uris.append(uri)
        if unreadable:
            raise VideoCapabilityError(
                "video_reference_audio_unreadable", model=self._model, names=", ".join(unreadable)
            )
        return uris

    def _attach_reference_voices(self, reference_items: list[dict], request: VideoGenerationRequest) -> None:
        """把参考音频逐段挂到参考素材项的 ``reference_voice`` 字段上（就地修改）。

        对齐优先用 ``request.reference_audio_targets``（第 i 段音频对应 ``reference_items``
        的哪个下标）——参考音频的顺序是台词 speaker 首现顺序，参考图的顺序是 mention 首现
        顺序，两者独立派生，编排层（``reference_video`` 渲染管线）已算出「谁的声音配哪张图」
        的映射，此处不得自行按位置重新猜测。``reference_audio_targets`` 为 ``None`` 时回退
        按位置对齐（第 N 段音频挂第 N 个参考素材）——两侧同序本身不是契约，回退仅服务未经
        编排层填充的调用方（如手写测试）。

        音频段数多于可挂载的参考素材时硬失败而非丢弃多余段：丢弃会让某个角色的音色声明无声
        失效，用户直到成片才发现该角色声音仍是随机的，且已照常扣费。``reference_audio_targets``
        携带越界下标同样按此硬失败——那意味着编排层算出的映射与实际随请求发出的参考图对不上，
        必须暴露而非静默吞掉。
        """
        audio_files = list(request.reference_audio_files or [])
        if not audio_files:
            return
        if self._video_capabilities.reference_audio_mode == ReferenceAudioMode.NONE:
            raise VideoCapabilityError("video_reference_audio_unsupported", provider=self.name, model=self._model)

        targets = request.reference_audio_targets
        if targets is not None:
            # 重复下标与越界下标同类错配：两段音频指向同一个参考素材项时，逐条赋值会静默
            # 覆盖前一条绑定，某个角色的音色声明无声丢失——必须硬失败，不能让它悄悄发生。
            valid = (
                len(targets) == len(audio_files)
                and len(set(targets)) == len(targets)
                and all(0 <= t < len(reference_items) for t in targets)
            )
        else:
            valid = len(audio_files) <= len(reference_items)
        if not valid:
            # 与 gate 的 video_reference_audio_exceeded 分成两个 code：那条的 limit 是模型的
            # 能力上限（减角色数就能过），这条的上限是该请求实际有几个可挂载的参考素材
            # （加参考图也能过）。共用一个 code 会让文案给出与实际卡点不符的处置建议。
            raise VideoCapabilityError(
                "video_reference_audio_slots_insufficient",
                provider=self.name,
                model=self._model,
                slots=len(reference_items),
                count=len(audio_files),
            )
        # 解码放在槽位校验之后：槽位不足是「这次请求的参考图不够挂」，比某段文件读不出来更靠前
        # 地说明卡点，先报它能让用户一次看到真正要改的东西。
        uris = self._decode_reference_audio_uris(request)
        if targets is not None:
            for idx, uri in zip(targets, uris, strict=True):
                reference_items[idx]["reference_voice"] = uri
        else:
            for item, uri in zip(reference_items, uris, strict=False):
                item["reference_voice"] = uri

    # ── HTTP submit / poll / download ───────────────────────────────────

    @with_retry_async(
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        retry_if=should_retry_submit,
    )
    async def _create_task(self, client: httpx.AsyncClient, payload: dict) -> str:
        # 创建任务是非幂等的「建任务 + 计费」POST：submit_post 把歧义传输错误（请求可能已送达
        # 服务端但响应在途丢失）转 AmbiguousSubmitError 终态失败，避免自动重试重复建任务 + 重复计费；
        # >=400 由其落 body 日志 + raise_for_status 抛 HTTPStatusError（保留 status_code 供咽喉层识别
        # 413 降档），交 should_retry_submit 按状态码分流——4xx fail-fast、5xx/429 重试。
        resp = await submit_post(
            lambda: client.post(
                f"{self._request_base_url}{_VIDEO_ENDPOINT}",
                json=payload,
                headers=dashscope_headers(self._api_key, async_mode=True),
            ),
            provider=PROVIDER_DASHSCOPE,
        )
        return extract_task_id(resp.json())

    async def _poll_once(self, client: httpx.AsyncClient, task_id: str, base_url: str) -> dict:
        resp = await client.get(
            f"{base_url}/tasks/{task_id}",
            headers=dashscope_headers(self._api_key),
        )
        resp.raise_for_status()
        return resp.json()

    async def _poll_and_build(
        self,
        client: httpx.AsyncClient,
        task_id: str,
        request: VideoGenerationRequest,
        *,
        is_resume: bool,
    ) -> VideoGenerationResult:
        # 续跑轮询回放提交时的域名：任务 id 只在创建它的 endpoint 上可查，用户在途改 base_url
        # 后按当下配置解析出的新域名去轮旧任务会 404，被下方的 404 分支误判成过期。
        base_url = request.submitted_base_url or self._request_base_url

        # resume 路径下 GET 返回 404（task 完全不存在）直接转 ResumeExpiredError，
        # 不走 poll_with_retry 重试。task_id 24h 过期表现为 200 + task_status=UNKNOWN，
        # 由下方 is_dashscope_expired 兜底（终态返回后判定）。
        async def _gated_poll() -> dict:
            try:
                return await self._poll_once(client, task_id, base_url)
            except httpx.HTTPStatusError as exc:
                if is_resume and exc.response.status_code == 404:
                    raise ResumeExpiredError(job_id=task_id, provider=PROVIDER_DASHSCOPE) from exc
                raise

        final = await poll_with_retry(
            poll_fn=_gated_poll,
            is_done=is_dashscope_terminal,
            is_failed=dashscope_failure_reason,
            poll_interval=DASHSCOPE_POLL_INTERVAL_SECONDS,
            max_wait=self._max_wait(request.duration_seconds),
            retry_if=should_retry_poll,
            label="DashScope",
            on_progress=lambda v, elapsed: logger.info(
                "DashScope 视频生成中... status=%s elapsed=%ds",
                (v.get("output") or {}).get("task_status"),
                int(elapsed),
            ),
        )

        if is_dashscope_expired(final):
            if is_resume:
                raise ResumeExpiredError(
                    job_id=task_id,
                    provider=PROVIDER_DASHSCOPE,
                    message=f"DashScope task expired: {task_id}",
                )
            raise RuntimeError(f"DashScope task expired during generate: {task_id}")

        video_url = extract_video_url(final)
        await self._download_with_retry(video_url, request.output_path)
        logger.info("DashScope 视频下载完成: %s", request.output_path)

        # usage.duration 是真实计费时长（wan2.7-r2v 含输入视频时长），缺失回落请求时长
        billing_duration = extract_billing_duration(final)
        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_DASHSCOPE,
            model=self._model,
            duration_seconds=billing_duration if billing_duration is not None else request.duration_seconds,
            video_uri=video_url,
            task_id=task_id,
            generate_audio=request.generate_audio,
        )

    @staticmethod
    @with_retry_async(
        max_attempts=DOWNLOAD_MAX_ATTEMPTS,
        backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        retry_if=should_retry_download,
    )
    async def _download_with_retry(video_url: str, output_path: Path) -> None:
        await download_video(video_url, output_path)

    @staticmethod
    def _max_wait(duration_seconds: int) -> float:
        return max(_MIN_POLL_TIMEOUT_SECONDS, duration_seconds * _POLL_TIMEOUT_PER_SECOND)
