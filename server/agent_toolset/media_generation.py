"""媒体生成工具的声明。

生成类工具是长任务：ArcReel Agent 等到批次终态，拿到 ``generation_result`` 与 ``batch_id``；外部 Agent
立即拿到 ``generation_batch`` 句柄并轮询。两种值由同一组结果钩子投影，终态的 ``generation_result`` 与
批次终态查询附带的生成结果同形。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, overload

from pydantic import BaseModel

from server.agent_toolset.declaration import BLOCKED, READ_CHECK, ScopedHandler, ToolDeclaration
from server.media_tools.assets import (
    GenerateAssetsRequest,
    ListPendingAssetsRequest,
    generate_assets,
    list_pending_assets,
)
from server.media_tools.context import (
    GenerationToolValue,
    generation_is_error,
    generation_structured,
    generation_summary,
)
from server.media_tools.image_edits import EditImagesRequest, edit_images
from server.media_tools.narration_audio import GenerateNarrationAudioRequest, generate_narration_audio
from server.media_tools.storyboards import GenerateStoryboardsRequest, generate_storyboards
from server.media_tools.videos import GenerateVideosRequest, generate_videos


@overload
def generation_tool[RequestT: BaseModel](
    *,
    name: str,
    description: str,
    request_model: type[RequestT],
    handler: ScopedHandler[RequestT, GenerationToolValue],
) -> ToolDeclaration[RequestT, GenerationToolValue]: ...


@overload
def generation_tool[RequestT: BaseModel, ResultT](
    *,
    name: str,
    description: str,
    request_model: type[RequestT],
    handler: ScopedHandler[RequestT, ResultT],
    summary: Callable[[ResultT], str | None],
    projection: Callable[[ResultT], dict[str, Any]],
    is_error: Callable[[ResultT], bool],
) -> ToolDeclaration[RequestT, ResultT]: ...


def generation_tool(
    *,
    name: str,
    description: str,
    request_model: type[BaseModel],
    handler: ScopedHandler[Any, Any],
    summary: Callable[[Any], str | None] = generation_summary,
    projection: Callable[[Any], dict[str, Any]] = generation_structured,
    is_error: Callable[[Any], bool] = generation_is_error,
) -> ToolDeclaration[Any, Any]:
    """生成类长任务的声明：入口阻断迁移失败的项目，结果缺省按生成结果钩子投影。

    成功值在生成结果之外还有别的形态（如宫格的规划预览）时，传入覆盖这一形态的三个钩子。
    """
    return ToolDeclaration(
        name=name,
        description=description,
        request_model=request_model,
        migration=BLOCKED,
        domain_key="generation_result",
        handler=handler,
        long_task=True,
        summary=summary,
        projection=projection,
        is_error=is_error,
    )


LIST_PENDING_ASSETS = ToolDeclaration(
    name="list_pending_assets",
    description=(
        "列出项目中尚无资产图的角色、场景、道具与商品（名称与描述摘要）；type 省略则汇总全部类型。只读，无副作用。"
        "项目数据升级失败时返回 project_migration_failed problem；按其明细修复后调用 retry_project_migration。"
    ),
    request_model=ListPendingAssetsRequest,
    migration=READ_CHECK,
    domain_key="list_pending_assets",
    handler=list_pending_assets,
)

GENERATE_ASSETS = generation_tool(
    name="generate_assets",
    description=(
        "批量生成角色 / 场景 / 道具 / 商品的资产图（付费生成，图片写入项目）。"
        "多个类型的资产纳入同一批次；只补缺图时，已失效但可用的旧图会被复用，不会自动重生。"
        "缺少 description 的资产逐个记为 blocked。"
        "终态结果的 generation_result 按 requested / succeeded / failed / blocked 逐 ID 给出结局，"
        "ID 形如 character/张三，每个失败项带稳定 code 与下一步动作。"
    ),
    request_model=GenerateAssetsRequest,
    handler=generate_assets,
)

GENERATE_STORYBOARDS = generation_tool(
    name="generate_storyboards",
    description=(
        "为说书（narration）或剧情演绎剧本生成分镜图（付费生成，图片写入项目 storyboards/）。"
        "只补缺图时，已失效但可用的旧图不会被自动重生。"
        "引用了未登记名称或所引用主体尚无资产图的分镜、缺少 image_prompt 的分镜逐个记为 blocked；"
        "生成失败的分镜另记录到 storyboards/generation_failures.json。"
        "终态结果的 generation_result 按 requested / succeeded / failed / blocked 逐 ID 给出结局，"
        "每个失败项带稳定 code 与下一步动作。"
    ),
    request_model=GenerateStoryboardsRequest,
    handler=generate_storyboards,
)

EDIT_IMAGES = generation_tool(
    name="edit_images",
    description=(
        "对已生成的资产图 / 分镜图做指令式局部编辑：保持原图大体不变，仅按指令修改不满意的部分"
        "（如换发色、去掉背景杂物、调整光线氛围），支持同类型批量下发（付费生成，编辑结果写入项目）。"
        "与「重新生成」的区别：编辑 = 保底图微调、不改变原 image_prompt，之后重新生成会按原 prompt 重画、"
        "作废本次编辑效果；重新生成 = 按原 prompt 整图重画，会推翻已满意的部分。"
        "用户只想改局部时用编辑；用户想推翻构图 / 内容重来、或原 image_prompt 本身要改时用重新生成。"
        "编辑必然走图生图（i2i）；当前项目图片供应商不支持 i2i 时每个 ID "
        "记为 blocked（image_capability_missing_i2i），不创建任何生成任务。"
        "编辑始终是显式选择（每条编辑自带指令），终态结果的 generation_result 按 "
        "requested / succeeded / failed / blocked 逐 ID 给出结局。"
    ),
    request_model=EditImagesRequest,
    handler=edit_images,
)

GENERATE_NARRATION_AUDIO = generation_tool(
    name="generate_narration_audio",
    description=(
        "为任意生成模式中由 narrator 拥有发声内容的单元显式生成旁白配音（TTS；付费生成，音频写入项目 audio/）。"
        "只补缺配音时，已失效但可用的旧配音不会被自动重生。"
        "显式点名的单元若发声不属于叙述旁白、未通过发声准入或没有可合成的旁白文本，逐个记为 blocked。"
        "终态结果的 generation_result 按 requested / succeeded / failed / blocked 逐 ID 给出结局，"
        "每个失败项带稳定 code 与下一步动作。"
        "合成文本在 worker 开始时从最新剧本的规范 narrator utterances 读取，不依赖分镜图或视频。"
    ),
    request_model=GenerateNarrationAudioRequest,
    handler=generate_narration_audio,
)

GENERATE_VIDEOS = generation_tool(
    name="generate_videos",
    description=(
        "为分镜图生视频或参考生视频项目生成视频片段（付费生成，视频写入项目）。"
        "已有可用成片（Manifest 认定 current / stale，或被选中的手动上传版本）默认复用。"
        "整批准入：任一目标未通过准入（缺素材、发声冲突、提示词待补、产物状态不可读等），"
        "或有目标需要用户确认跨档时长费用时，本次零任务入队、不产生批次，"
        "结果带 batch_admission 逐单元明细与 request_projections 请求投影；"
        "用户确认档位后把原目标集合一次性重发，不要按档位拆成多次调用。"
        "终态结果的 generation_result 按 requested / succeeded / failed / blocked 逐 ID 给出结局，"
        "每个失败项带稳定 code 与下一步动作。"
    ),
    request_model=GenerateVideosRequest,
    handler=generate_videos,
)

MEDIA_GENERATION_TOOLS = (
    LIST_PENDING_ASSETS,
    GENERATE_ASSETS,
    GENERATE_STORYBOARDS,
    EDIT_IMAGES,
    GENERATE_NARRATION_AUDIO,
    GENERATE_VIDEOS,
)

__all__ = ["MEDIA_GENERATION_TOOLS", "generation_tool"]
