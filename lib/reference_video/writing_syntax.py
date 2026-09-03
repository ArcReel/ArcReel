"""参考生视频「引用语法」规范——唯一真相源。

LLM 在 script_plan / prompt_authoring 产出的 unit 正文与人在编辑器里写的是同一种格式，因此语法规范
只能有一份措辞：:func:`writing_syntax_spec` 由两级 prompt builder
（``build_reference_units_split_prompt`` / ``build_reference_video_prompt``）共同注入，
agent 侧文档只留概览并指向工具，前端语法提示另走 i18n 三语，均不复制全文。

放在 ``lib/reference_video/`` 与 :mod:`lib.reference_video.text_parser` 同域：规范措辞与
执行它的解析器同进同退，改语法时两者在同一目录里对照修改。

其中的场景引用规则同时要进 ``split-reference-video-units`` 子智能体，故正文存放在
``agent_runtime_profile/.claude/references/reference-video-scene-rules.md``，由
:func:`scene_reference_rules` 读入拼进本规范；子智能体直接读同一个文件。
"""

from __future__ import annotations

from lib.agent_profile import read_profile_reference

#: 场景引用规则文件在 ``.claude/references/`` 下的文件名。
SCENE_REFERENCE_RULES_FILE = "reference-video-scene-rules.md"

_SYNTAX_HEAD = """每个视频单元的正文是一段**自由书写的文本**，按行书写即可。

记号只有三种，与输出语言无关，可出现在正文任意位置：

1. `@[名称]`——引用一件登记资产（商品 / 角色 / 场景 / 道具），表示它出现在画面里。
   名称逐字取自候选表，不要发明表外的名称。
2. `@[角色名]{台词内容}`——该角色说这句话。花括号 `{}` 里是逐字台词；角色名必须是已登记的
   角色资产名。`@[名称]` 与 `{` 之间允许有空白或一个冒号，`@[李明]{…}`、`@[李明] {…}`、
   `@[李明]：{…}` 三种写法等价。
3. `{台词内容}`——画外音（不指定说话人）。花括号前没有紧邻的 `@[名称]` 即为此形。
   这类无归属旁白不下发视频模型，由 TTS / 后期配音承担；正文里照常写，它只是不进画面生成。

台词记号紧跟它所对应的那句动作：写在同一行末尾（`@[李明] 推开门。@[李明]{我来了。}`）或紧接的下一行。
一行可以有多个记号。不进画面参考图的只有台词记号里的说话人位；某个角色若全文只出现在说话人位上，
它就只提供声音。写在记号之外的 `@[名称]` 照常进入画面参考图，与同一行有没有台词无关。

结构标记不随输出语言变化：`@[]` 与花括号 `{}` 是解析器认的语法记号，任何语言的项目都逐字
保持这两个记号，只有记号之间的画面描述与台词内容用项目语言书写。

硬性约束（违反即整份产出被拒）：

- 花括号只用于台词与画外音，必须成对闭合、不嵌套、内容非空；
  **画面描述里不得出现游离的 `{` 或 `}`**——没被识别成台词记号的花括号会原样进入画面描述。
- 正文必须有画面描述，不能只有台词与画外音。
- 语法记号一律半角：`@[名称]` 的方括号成对闭合且名称非空，台词的花括号写 `{}` 不写 `｛｝`。
- 每个 `@[名称]` 都必须是候选表中已登记的资产名，台词的角色名同样如此。
- 画面描述不写外貌 / 服装 / 场景陈设 / 色调光影——静态外观由参考图承担；
  泛指群演（老人甲 / 村民若干）直接写进描述即可，不用 `@` 引用。

"""

_SYNTAX_TAIL = """

示例（一个视频单元的正文）：

```
@[李明] 推开 @[酒馆] 的木门，侧身跨过门槛。@[李明]{这地方比我想的还热闹。}
他走向柜台，把 @[长剑] 横放在台面上，指节在剑鞘上叩了两下。
{他知道，今晚不会太平。}
```"""


def scene_reference_rules() -> str:
    return read_profile_reference(SCENE_REFERENCE_RULES_FILE)


def writing_syntax_spec() -> str:
    return _SYNTAX_HEAD + scene_reference_rules() + _SYNTAX_TAIL


__all__ = ["SCENE_REFERENCE_RULES_FILE", "scene_reference_rules", "writing_syntax_spec"]
