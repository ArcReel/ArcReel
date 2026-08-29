"""角色声音绑定方式：项目级设置，决定角色的声音靠什么约束。

``prompt``（默认）只把角色的 ``voice_style`` 写进提示词做软约束；``reference_audio`` 才把角色
已设的参考音频随请求挂给视频模型，换来跨片段的原生音色一致。两条路径的差别一路由
:func:`lib.config.resolver.derive_voice_consistency` 收口成 ``voice_consistency`` 档位，渲染层、
脚本预览与执行期挂线都只读那一位，本模块因此只提供取值与归一，不重复判定。

本模块是叶子模块（不依赖 lib 内任何其它模块），供 lib.config 及其上各层与 server 侧共同引用。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast, get_args

#: 角色声音绑定方式。前端 ``CharacterVoiceBinding`` 与之一一对应，取值增减须两侧同步。
CharacterVoiceBinding = Literal["prompt", "reference_audio"]

#: 未声明时的取值：提示词软约束。参考音频是可选增强，须由用户显式选择才生效。
DEFAULT_CHARACTER_VOICE_BINDING: CharacterVoiceBinding = "prompt"

VALID_CHARACTER_VOICE_BINDINGS: frozenset[str] = frozenset(get_args(CharacterVoiceBinding))

PROJECT_FIELD = "character_voice_binding"


def character_voice_binding(project: Mapping[str, object] | None) -> CharacterVoiceBinding | None:
    """项目声明的角色声音绑定方式；无项目上下文返回 None。

    返回 None 而非默认档，口径与 :func:`lib.config.resolver.caps_generation_mode` 一致：模型目录
    等无项目上下文的查询不该被渲染成用户显式选过某种绑定方式。

    project.json 是明文文件，字段被手编成非法值时按默认档（提示词软约束）解读——这一侧的降级只会
    少挂参考音频，比按参考音频档解读一个读不懂的值安全。
    """
    if project is None:
        return None
    raw = project.get(PROJECT_FIELD)
    if isinstance(raw, str) and raw in VALID_CHARACTER_VOICE_BINDINGS:
        return cast(CharacterVoiceBinding, raw)
    return DEFAULT_CHARACTER_VOICE_BINDING


__all__ = [
    "DEFAULT_CHARACTER_VOICE_BINDING",
    "PROJECT_FIELD",
    "VALID_CHARACTER_VOICE_BINDINGS",
    "CharacterVoiceBinding",
    "character_voice_binding",
]
