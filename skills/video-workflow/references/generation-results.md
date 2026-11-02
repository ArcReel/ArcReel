# 生成结果

生成工具返回按 ID 划分的结构化结果。读取结构，不概括面向人的说明文本。

选择有三种语义：

- 非空 ID 列表表示显式选择任务，并可能强制重新生成。
- 省略 ID 字段表示只补齐缺失产物。
- 空 ID 列表无效，应省略该字段。

请求的 ID 会被划分到 `succeeded`、`failed` 和 `blocked`；`skipped` 记录不属于该请求划分的复用结果。使用结构化 problem code 和 action 报告每个 ID，不根据散文解释自动重试。

状态报告必须区分四个独立维度：

- 工作流步骤状态；
- 任务或队列状态；
- 供应商提交检查点；
- 产物状态（`current`、`stale`、`missing` 或 `blocked`）。

任务成功不代表产物与当前输入一致；产物缺失不代表付费任务失败；已提交供应商的任务可能已经计费；不得把 blocked 产物当作 missing 后重新生成。

供应商审核拒绝输入图片（`provider_input_image_rejected`、`provider_input_image_rejected_at`）时，转述原因并说明可以把相关图片上传到供应商素材库，由用户决定。只有用户明确要求上传某张图片时，才调用 `upload_to_provider_asset_library` 并传 `user_requested=true`；拒绝本身不构成上传授权，也不授权自动重新生成。上传立即返回入库任务，用 `get_provider_asset_links` 查询状态，状态为 `active` 后再按用户意愿重新生成；`provider_asset_link_processing` 与 `provider_asset_link_failed` 表示入库尚未完成或失败，转述给用户，不自动解除关联或重传。
