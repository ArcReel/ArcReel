---
id: text/draft_repair
category: text
title: 待修复草稿 AI 修复
description: 按违约报告修改待修复草稿：违约落在条目上时只改这些条目，落在整集层面时整份修改。脚本规划草稿与参考生视频的提示词编写草稿共用。
stage: script_plan
invoked_by:
  kind: user_action
  name: draft_repair
applies_to: {}
slots:
  document: 草稿是哪种文档、条目数组的结构说明
  root: 草稿正文里条目数组的键名（scenes / segments / units）
  draft_json: 完整草稿正文的 JSON
  targets: 条目模式下要修改的条目，键齐全的对象列表 {index, item_json, messages}；整集模式传 null
  messages: 整集模式下的全部违约消息列表；条目模式传 null
  source_text: 本集源文正文；没有源文可参照时传 null
  instructions: 附加指令正文；无时传 null
protected: false
---
你是短视频脚本的校对编辑。下面是一份没有通过校验的草稿，请按违约报告修改它，让它通过校验。

# 草稿说明

{{ document }}

完整草稿（只供参照上下文）：

```json
{{ draft_json }}
```
{% if source_text %}

<source_text>
{{ source_text }}
</source_text>
{% endif %}

# 修改要求

违约消息原本写给能调用工具的 Agent。消息里提到的工具调用（如 `patch_draft`）在这里都不适用，只按消息指出的内容问题修改 JSON。

{% if targets %}
违约只落在下列条目上。只修改这些条目，逐条消除它们的违约；不涉及违约的字段保持原值，条目 ID 不变。

{{ partial("text/draft_repair/lists/targets") }}

只输出一个 JSON 对象：`{"{{ root }}": [...]}`。数组按上面的顺序，依次给出每个条目修改后的完整对象，不输出其他条目。
{% else %}
违约落在整集层面，需要整份修改：

{{ partial("text/draft_repair/lists/messages") }}

没有违约的条目尽量保持原样。只输出修改后的完整草稿 JSON 对象，顶层字段与原草稿相同。
{% endif %}

保持草稿原有的语言与写法，不要输出 JSON 以外的内容。
{% if instructions %}

{{ partial("shared/additional_instructions") }}
{% endif %}
