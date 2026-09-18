"""ComfyUI 端点在端点测试里的两种模式：预览请求与测试连接。

预览请求渲染的是运行时真会 POST 给 ``/prompt`` 的那一份请求体——同一个 :func:`build_workflow`、
同一份 ``auth`` 渲染、同一个 ``client_id`` 形状，差别只有三处且都不改语义：凭证以打码值进
``auth`` 渲染、素材换成体积摘要（预览不上传，服务端认的引用名要到上传响应里才有）、``prompt_id``
保持占位符。声明式那一侧的三处替换与此一一对应。

请求体之外还附一段换算说明：尺寸、帧数、种子与「这次改图删了哪些节点」都是算出来的，光看一份
几十个节点的 JSON 没法回答「我选的 720p 到底变成了多少像素」。

「验证响应」不在这里——ComfyUI 的产物提取读的是 ``output`` 绑定那个节点的固定三个键，没有用户
可配的取值路径可验（``docs/adr/0081``），模式矩阵在 :mod:`.modes` 里已把它挡掉。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from random import Random
from secrets import token_hex
from typing import Any
from urllib.parse import urlencode

from lib.custom_provider.auth_section import declares_credentials, render_auth
from lib.custom_provider.comfyui.request_builder import BuiltWorkflow, MediaInputs, build_workflow
from lib.custom_provider.comfyui_backend import ComfyuiVideoBackend
from lib.custom_provider.comfyui_client import client_id_for, normalize_comfyui_base_url

from .inputs import EndpointTestAssets, EndpointTestCredentials, EndpointTestParameters
from .preview import (
    UNRESOLVED_API_KEY,
    UNRESOLVED_BASE_URL,
    PreviewedRequest,
    RequestPreview,
    masked_api_key,
    restore_mask_in_url,
)
from .trial_run import TrialRunTarget, provider_from_base_url

#: 提交之后才有的值：预览恒保持占位符，与声明式那一侧的 ``{{ task_id }}`` 同形。
UNRESOLVED_PROMPT_ID = "{{ prompt_id }}"

#: 测试连接发出的那一笔在 ComfyUI 上的名字前缀，接一段随机后缀。
_TRIAL_RUN_LABEL_PREFIX = "endpoint-test-"


@dataclass(frozen=True)
class ComfyuiConversions:
    """预览请求附的换算说明：这份 workflow 里的几个值是怎么从一次生成请求算出来的。

    ``width`` / ``height`` / ``frames`` / ``seed`` 为 ``None`` 表示这一维没有驱动这份 workflow
    （未绑定，或尺寸这一维只绑了一侧因而判为固定），workflow 里的字面值原样保留——这正是用户最
    需要看见的一格：他在项目页选的分辨率对这个端点根本不起作用。
    """

    #: 实发 workflow 的指纹，与成片版本元数据里记的是同一个值，可据此核对某一版是照哪份图出的。
    workflow_sha256: str
    #: 换算的输入：请求侧的比例、分辨率档与时长。
    aspect_ratio: str
    resolution: str | None
    duration_seconds: float | None
    #: 换算的产物：填进 workflow 的像素、帧数与种子。
    width: int | None
    height: int | None
    frames: int | None
    seed: int | None
    #: 从正文里拆出、写进负向节点的排除项文本。
    negative_prompt: str
    #: 这次按参考图张数改图删掉的节点。空元组即没删。
    dropped_nodes: tuple[str, ...]

    @classmethod
    def of(cls, built: BuiltWorkflow, parameters: EndpointTestParameters) -> ComfyuiConversions:
        return cls(
            workflow_sha256=built.workflow_sha256,
            aspect_ratio=parameters.aspect_ratio,
            resolution=parameters.resolution,
            duration_seconds=parameters.duration_seconds,
            width=built.width,
            height=built.height,
            frames=built.frames,
            seed=built.seed,
            negative_prompt=built.negative_prompt,
            dropped_nodes=built.dropped_nodes,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "workflow_sha256": self.workflow_sha256,
            "aspect_ratio": self.aspect_ratio,
            "resolution": self.resolution,
            "duration_seconds": self.duration_seconds,
            "width": self.width,
            "height": self.height,
            "frames": self.frames,
            "seed": self.seed,
            "negative_prompt": self.negative_prompt,
            "dropped_nodes": list(self.dropped_nodes),
        }


def preview_comfyui_request(
    definition: Mapping[str, Any],
    parameters: EndpointTestParameters,
    *,
    credentials: EndpointTestCredentials | None = None,
    assets: EndpointTestAssets | None = None,
    placeholder_missing_assets: bool = True,
    rng: Random | None = None,
) -> RequestPreview:
    """渲染 ``POST /prompt`` 的请求体与随它的换算说明。定义须已过共享校验器。

    轮询那一节一并给出（``GET /history/{prompt_id}``）：它与提交带的是同一份凭据，而 ComfyUI
    多半套在反向代理后面，用户要核的正是「这组头会不会原样发到每一条路由上」。产物下载那一节
    不给——``/view`` 的查询参数要到产物条目回来之后才知道，凭空编一个文件名不是预览。

    ``rng`` 是随机种子的注入点，与 :func:`build_workflow` 同一个缝；``policy: random`` 的条目每次
    预览都会给出一个新的种子，那正是真提交时会发生的事。
    """
    api_key = masked_api_key(credentials.api_key) if credentials else UNRESOLVED_API_KEY
    base_url = normalize_comfyui_base_url(credentials.base_url) if credentials and credentials.base_url else ""
    headers, query = render_auth(definition.get("auth") or {}, api_key=api_key)
    built = build_workflow(
        definition,
        prompt=parameters.prompt,
        aspect_ratio=parameters.aspect_ratio,
        resolution=parameters.resolution,
        # 时长原样转交，与 backend 同一处置：``frames`` 未绑定或读不到帧率时构造层自己跳过换算。
        duration_seconds=parameters.duration_seconds,
        media=_preview_media(definition, assets, placeholder_missing=placeholder_missing_assets),
        rng=rng,
    )
    return RequestPreview(
        submit=PreviewedRequest(
            method="POST",
            url=_preview_url(base_url, "/prompt", query),
            headers=dict(headers),
            # ``client_id`` 与真发同一个构造：预览给出的必须是真发的那个形状，而这一串正是用户
            # 在 ComfyUI 队列界面上认 ArcReel 的记号。
            body={"prompt": built.workflow, "client_id": client_id_for(UNRESOLVED_PROMPT_ID)},
        ),
        poll=PreviewedRequest(
            method="GET",
            url=_preview_url(base_url, f"/history/{UNRESOLVED_PROMPT_ID}", query),
            headers=dict(headers),
            body=None,
        ),
        result=None,
        conversions=ComfyuiConversions.of(built, parameters).to_payload(),
    )


def comfyui_credential_needs(definition: Mapping[str, Any]) -> tuple[bool, bool]:
    """这份定义要不要 ``base_url`` / ``api_key``。

    ``base_url`` 恒需要：ComfyUI 的路由全在服务地址根下，定义里一个绝对地址都不写。``api_key``
    只在 ``auth`` 节真会发出凭据时需要——ComfyUI 原生无鉴权，凭据只有套在反向代理后面的部署才
    有。「非空」按两张表里有没有条目算（:func:`declares_credentials`），不按 ``auth`` 这个对象
    本身算：``{"headers": {}}`` 是合法定义、渲染出来一个头都没有，为它索要 api_key 会把一台不
    设防的 ComfyUI 挡在预览与测试连接之外。
    """
    return True, declares_credentials(definition.get("auth") or {})


def comfyui_target(
    definition: Mapping[str, Any],
    credentials: EndpointTestCredentials,
    parameters: EndpointTestParameters,
    *,
    provider: str | None = None,
) -> TrialRunTarget:
    """内联 ComfyUI 定义的目标：直接构造视频 backend，跑的是生产那一条路。

    不跑能力闸（见 :attr:`TrialRunTarget.gate_capabilities`）：ComfyUI 端点费用固定 0，能力又只
    从节点绑定推导，而内联定义这条入口拿不到推导结果。

    结果体里不放渲染后的提交请求：实发 workflow 的随机种子与素材引用名要到提交那一刻才定下来，
    把一份预览摆进结果体会让用户以为提交的就是它。请求形状去预览请求那张卡看。
    """
    label = provider or provider_from_base_url(credentials.base_url)
    # 这一笔在 ComfyUI 队列界面上的名字（``client_id`` 是 ``arcreel-<job_label>``，上传的素材
    # 也按它命名）。测试连接不走 worker，没有 task_id，backend 的回落值是一串随机 hex——用户
    # 在自己手动跑的队列里认不出哪一笔是刚点的「测试连接」。后缀保留一段随机串：同一个端点可以
    # 被连着测好几次，重名会让上传的素材互相覆盖。
    job_label = f"{_TRIAL_RUN_LABEL_PREFIX}{token_hex(4)}"

    async def build() -> ComfyuiVideoBackend:
        return ComfyuiVideoBackend(
            provider_id=label,
            model=parameters.model,
            base_url=credentials.base_url,
            api_key=credentials.api_key,
            definition=definition,
            job_label=job_label,
        )

    return TrialRunTarget(
        provider=label,
        model=parameters.model,
        build_backend=build,
        definition=definition,
        gate_capabilities=False,
    )


def _preview_url(base_url: str, path: str, query: Mapping[str, str]) -> str:
    """一条路由的预览地址。凭证缺席时主机段保持占位符，请求形状不因此塌掉。"""
    url = f"{base_url or UNRESOLVED_BASE_URL}{path}"
    return restore_mask_in_url(f"{url}?{urlencode(query)}") if query else url


def _preview_media(
    definition: Mapping[str, Any],
    assets: EndpointTestAssets | None,
    *,
    placeholder_missing: bool,
) -> MediaInputs:
    """把素材换成摘要填进读图节点。

    ``placeholder_missing`` 为 True 时（独立预览），绑定了却没上传的格子按声明生成占位摘要：
    留空会让改图那一步把这些读图节点连同下游删掉，预览出来的就是一份缺了半条链路的图，而真发时
    用户是会带上素材的。参考图只按**实际上传的张数**填——张数少于格子数正是会触发改图的那种输入，
    删了哪些节点由换算说明给出，这一格恰恰是预览最该说清的事。
    """
    bindings: Mapping[str, Any] = definition.get("bindings") or {}
    uploaded = assets.items("reference_images") if assets else []
    slots = len(_targets(bindings.get("reference_images")))
    if uploaded:
        references = tuple(_summary("reference_images", item.content) for item in uploaded[:slots])
    else:
        references = tuple(_placeholder("reference_images") for _ in range(slots)) if placeholder_missing else ()
    return MediaInputs(
        start_image=_single_media(assets, bindings, "start_image", placeholder_missing=placeholder_missing),
        end_image=_single_media(assets, bindings, "end_image", placeholder_missing=placeholder_missing),
        reference_images=references,
    )


def _single_media(
    assets: EndpointTestAssets | None,
    bindings: Mapping[str, Any],
    key: str,
    *,
    placeholder_missing: bool,
) -> str | None:
    if not _targets(bindings.get(key)):
        return None
    raw = assets.single(key) if assets else None
    if raw is not None:
        return _summary(key, raw.content)
    return _placeholder(key) if placeholder_missing else None


def _summary(key: str, content: bytes) -> str:
    return f"<{key}, {len(content)} bytes>"


def _placeholder(key: str) -> str:
    return f"<{key} not uploaded>"


def _targets(raw: object) -> list[Mapping[str, Any]]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    return [target for target in raw if isinstance(target, Mapping)]
