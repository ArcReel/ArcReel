"""参考生视频「引用语法」规范的源文读取。

LLM 在 script_plan / prompt_authoring 产出的 unit 正文与人在编辑器里写的是同一种格式，因此语法规范
只有一份措辞：内置共享模版片段 ``shared/writing_syntax``。两级参考视频模版直接引用该片段；
:func:`writing_syntax_spec` 读取同一份源文，供字符串拼接的广告 builder 使用。agent 侧文档只留概览，
前端语法提示另走 i18n 三语，均不复制全文。
"""

from __future__ import annotations

from lib.prompt_templates.builtin import BUILTIN_DIRECTORY

_WRITING_SYNTAX = (BUILTIN_DIRECTORY / "partials/shared/writing_syntax.md").read_text(encoding="utf-8")


def writing_syntax_spec() -> str:
    return _WRITING_SYNTAX


__all__ = ["writing_syntax_spec"]
