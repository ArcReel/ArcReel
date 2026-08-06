# Reference-video 时长确认

视频工具首次返回时长确认清单时，尚未入队任何需要确认的任务。

## 处理

1. 逐 unit 展示：剧本编排时长、实际申请时长、成片会更长还是更短。
2. 成片会短于编排且内容明显装不下时，优先建议回到 step1 拆分 unit。
3. 用户接受清单后，用**完全相同的工具、script、选择范围和 resume 参数**重试，只追加：

```json
{"confirm_duration": true}
```

4. 用户不接受时，不入队；按其选择修改 step1 或停止。

## 完成条件

- 确认前状态是 `NEEDS_CONFIRMATION`，不能报告为已生成；
- 确认后工具实际入队并返回逐项结果；
- 再次调用 workflow status，所有请求 unit 均 current、failed 或 blocked。
