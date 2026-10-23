"""视频模型的提示词方言：参考素材指代与台词句式的逐模型写法。

参考生视频的最终 prompt 由 ``reference_video/unit`` 模版拼成三段论——主体绑定、单元正文、
风格与负向约束。三段里的**参考素材指代**与**台词句式**并非所有视频模型都通用的语法：

- Seedance 官方按 ``Image N`` / ``Audio N`` 指代参考素材，台词写成「说话人 + 说话动词 + 引号内容」；
- 万相 3.0 官方按 ``图N`` / ``音频N`` 指代，台词同样用引号，负向约束前缀写 ``Negative:``；
- MiniMax H3 官方示例写 ``reference images 1 and 2``，前端产品统一用 ``@Image1`` / ``@Audio1``。

ArcReel 早先自有一套 ``@图片N`` / ``@音频N`` / ``<X>说 {台词}`` 的记号。它在中转端点与
自有实现上够用，但对按官方格式训练的两家模型属于陌生语法——参考图绑定不被认领会让角色
一致性直接掉落，台词花括号不被当成引语会让口型与音频错位。故把这两类写法收进本模块，
渲染层按方言拼串。

识别的口径与视频通道一致（复用 :func:`lib.backends.video_backends.name_family.capability_names`：
按目录条目的官方模型名与别名比对）。认不得的模型回落 :data:`GENERIC`，输出与本模块引入
**之前逐字一致**——宁可退回通用写法，也不按模型 ID 的子串猜，猜错比不猜更贵。
"""

from __future__ import annotations

from dataclasses import dataclass

from lib.backends.video_backends.name_family import capability_names


@dataclass(frozen=True, slots=True)
class PromptDialect:
    """一套提示词写法。``*_ref`` / ``speech`` 是 ``str.format`` 模板，占位符见各字段说明。"""

    #: 方言名，进日志与测试断言用
    name: str
    #: 参考图指代模板，``{n}`` 是图号（从 1 起，与随请求发出的参考图严格同序）
    image_ref: str
    #: 参考音频指代模板，``{n}`` 是音频号（从 1 起）
    audio_ref: str
    #: 台词句式模板，``{subject}`` 是主体名、``{text}`` 是逐字台词
    speech: str
    #: 负向约束行的前缀
    negative_prefix: str

    def image(self, number: int) -> str:
        """第 ``number`` 张参考图的指代文本。"""
        return self.image_ref.format(n=number)

    def audio(self, number: int) -> str:
        """第 ``number`` 段参考音频的指代文本。"""
        return self.audio_ref.format(n=number)

    def say(self, subject: str, text: str) -> str:
        """一个发声记号的渲染结果：``subject`` 是主体名（不含尖括号），``text`` 是逐字台词。"""
        return self.speech.format(subject=subject, text=text)


#: 方言化之前的原写法。认不得的模型一律用它，保证既有项目的成片与预览不因本模块的存在而改变。
GENERIC = PromptDialect(
    name="generic",
    image_ref="@图片{n}",
    audio_ref="@音频{n}",
    speech="<{subject}>说 {{{text}}}",
    negative_prefix="Avoid:",
)

#: Seedance 系列（豆包）。官方指代写 ``Image N`` / ``Audio N``，台词按「说话人 + 说 + 引号内容」。
SEEDANCE = PromptDialect(
    name="seedance",
    image_ref="Image {n}",
    audio_ref="Audio {n}",
    speech='<{subject}>说："{text}"',
    negative_prefix="Avoid:",
)

#: 万相 3.0 系列。官方指代写 ``图N`` / ``音频N``，负向约束前缀写 ``Negative:``。
WAN30 = PromptDialect(
    name="wan3",
    image_ref="图{n}",
    audio_ref="音频{n}",
    speech='<{subject}>说："{text}"',
    negative_prefix="Negative:",
)

#: MiniMax H3（Hailuo 3）的多模态参考。官方 API 示例正文写
#: ``the character's appearance follows reference images 1 and 2``（英文序数 + 编号），
#: 而 H3 的前端产品统一教用户输入 ``@Image1`` / ``@Video1`` / ``@Audio1``，故取更短、
#: 且被广泛接受的 ``@ImageN`` 写法。
#:
#: 与其它两家的关键差别：H3 **原生音频常开**，台词必须写进正文并加引号（口型与语音都由
#: 这一次生成产出），没有独立的台词字段；官方 V2 文档也**不提供方括号运镜指令**，
#: 镜头语言一律用自然语言描述。另注意 ``prompt_optimizer`` 只存在于 legacy Hailuo 接口
#: （默认开启、会改写整段 prompt），本方言不覆盖 legacy 模型。
MINIMAX_H3 = PromptDialect(
    name="minimax-h3",
    image_ref="@Image{n}",
    audio_ref="@Audio{n}",
    speech='<{subject}>说："{text}"',
    negative_prefix="Avoid:",
)

#: 按名字族认模型：``(子串, 方言)``。顺序即优先级，先命中先用。
#: 只收官方模型名与别名里稳定出现的片段——``wan2.7`` 不含 ``wan3``，不会误认成 3.0；
#: ``MiniMax-Hailuo-2.3`` 不含 ``minimax-h3``，不会被认成 H3。
_DIALECT_MARKERS: tuple[tuple[str, PromptDialect], ...] = (
    ("seedance", SEEDANCE),
    ("minimax-h3", MINIMAX_H3),
    ("wan3", WAN30),
    ("wan-3", WAN30),
)


def dialect_for(model_id: str | None) -> PromptDialect:
    """按模型名取方言；认不得（含 ``None``）时回落 :data:`GENERIC`。

    比对名取自目录条目的官方模型名与别名（同视频通道的能力认定口径），所以自定义供应商写的
    上游模型 ID（如 ``doubao-seedance-2-5-260628``）与内置条目的短名（``doubao-seedance-2.5``）
    都能认出来。
    """
    if not model_id:
        return GENERIC
    for name in capability_names(model_id):
        lowered = name.lower()
        for marker, dialect in _DIALECT_MARKERS:
            if marker in lowered:
                return dialect
    return GENERIC
