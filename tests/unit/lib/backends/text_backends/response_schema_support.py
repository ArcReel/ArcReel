"""作为 ``response_schema`` 发给文本模型的全部结构化输出模型，供 schema 内容用例参数化。

新增结构化输出调用点时把它的模型补进 :func:`response_schema_models`，让 schema 内容约束覆盖到它。
"""

import pytest
from pydantic import BaseModel


def response_schema_models() -> list[type[BaseModel]]:
    """各调用点实际传入的 response_schema；动态模型按多值与单值时长枚举各取一份。"""
    from lib.episode.episode_planner import DramaPlanDraft, NarrationPlanDraft
    from lib.project.project_manager import ProjectOverview
    from lib.script.script_models import (
        AdReferenceFlatScript,
        AdVisualScript,
        DramaVisualScript,
        NarrationScriptPlanDraft,
        NarrationVisualEpisodeScript,
        ReferencePromptAuthoringFlatScript,
        build_drama_normalized_script_model,
        build_episode_script_model,
        build_reference_units_script_plan_model,
        with_new_assets_field,
    )

    models: list[type[BaseModel]] = [
        # 分集规划
        NarrationPlanDraft,
        DramaPlanDraft,
        # 分镜拆分（旁白）与提示词编写（参考生视频）
        NarrationScriptPlanDraft,
        ReferencePromptAuthoringFlatScript,
        # 提示词编写视觉层
        NarrationVisualEpisodeScript,
        DramaVisualScript,
        AdVisualScript,
        # 广告剧本（参考生视频）
        AdReferenceFlatScript,
        # 项目概述
        ProjectOverview,
    ]
    for durations in ([4, 6, 8], [8]):
        models += [
            build_drama_normalized_script_model(durations),
            build_reference_units_script_plan_model(durations),
            with_new_assets_field(build_episode_script_model("ad", durations)),
        ]
    return models


def response_schema_params() -> list:
    """:func:`response_schema_models` 的 pytest 参数形态；动态模型同名，id 带序号区分。"""
    return [pytest.param(model, id=f"{i}-{model.__name__}") for i, model in enumerate(response_schema_models())]
