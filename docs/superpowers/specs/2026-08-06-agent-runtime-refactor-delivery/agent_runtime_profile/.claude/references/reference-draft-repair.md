# Reference-video 隔离草稿修复

只在工具返回 `step1_reference_units.invalid.json` 或 `step2_reference_script.invalid.json` 时进入本流程。已付费产出保留在隔离草稿中，正式文件没有被污染。

## 修复循环

1. Read 工具返回的隔离草稿绝对路径。
2. 按 `violations[]` 的 `label`、`code` 和字段路径定位每一项。
3. Edit 只修改草稿允许的内容：
   - step1：`content.units[i].text`、`source_text`、`duration_seconds`；
   - step2：工具报告中明确允许修改的视觉正文。
4. 调 `mcp__arcreel__validate_and_promote_reference_draft({"episode": N})`。
5. 仍有 violations 就继续修同一草稿；不重新调用付费生成工具抽取另一份结果。

## Step1 书写层

正文只使用三类行：

```text
镜头N：画面与动作，资产写作 @[名称]
@[角色名]：{角色台词}
{画外音}
```

- 每 unit 最多 4 个镜头；
- 资产名逐字来自 `project.json`；
- `source_text` 是源文连续逐字子串；
- `unit_id`、`shots`、`references` 由服务端派生，不在草稿中手写；
- 时长取当前引用状态对应的合法档位。

## 完成条件

- 晋升工具成功；
- 隔离草稿已清除；
- 正式文件存在且 revision 更新；
- workflow status 不再报告 invalid-draft blocker；
- step1 内容变更后审核门回到 pending，最终剧本按 provenance 变为 stale。
