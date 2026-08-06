---
name: analyze-assets
description: 资产清单：从项目源文本提取并登记角色、场景和道具，完成允许为空的资产清单。
---

你负责一次完整的资产清单分析。原文由你读取，主 agent 只接收摘要。

## 输入

主 agent 提供分析范围；未指定时处理 workflow status 指向的全部 source scope。

## 步骤

1. 调 `mcp__arcreel__get_workflow_status({})`，记录 `source_revision`、content mode、目标 scope 与已有资产。
2. Read `project.json`，取得 `source_kind`、`source_language`、style、overview 和已有名称。
3. Glob 并 Read scope 内源文件，保持源语言；返回时不复制原文段落。
4. 提取三个 bucket：
   - **角色**：只写稳定视觉身份、外貌、服装、标志物与 `voice_style`；
   - **场景**：只写空间、光线、色调与标志结构，不把人物和剧情写进环境 sheet；
   - **道具**：只写可识别外观、材质、尺寸和色彩。
5. `source_kind=novel` 时可从实质出场推断具名角色；`source_kind=screenplay` 时只登记作者明确写下、可定型立绘的具名角色。编号群演、群体量词、纯泛称和空镜不登记。
6. 已有名称默认保留。只有主 agent 明确传入“修订已有资产”时才发送更新；否则在调用前过滤。
7. 每个非空 bucket 调一次 `mcp__arcreel__patch_project`。源文缺少视觉信息时写“需补充……”占位，不替作者编造具体外形。
8. 调：

```text
mcp__arcreel__complete_asset_inventory({
  "scope": <workflow status 返回的 scope>,
  "expected_source_revision": "<source_revision>"
})
```

空角色、空场景或空道具仍可完成 inventory。
9. 再调 `get_workflow_status`，验证 inventory current，且每个 submitted 名称均已持久化。合并数与预期不符时列为 concern。

## 完成条件

- 三个 bucket 都被分析并得到“新增、保留、按规则跳过或确认为空”的结论；
- `complete_asset_inventory` 成功且 source revision 未变化；
- 所有 submitted 名称都能在 `project.json` 找到；
- 返回中不含未解释候选。

## 返回

```text
状态: DONE | DONE_WITH_CONCERNS | BLOCKED
source revision: ...
新增: characters N / scenes N / props N
保留: ...
按规则跳过: ...
空 bucket: ...
验证后的 next state: ...
```
