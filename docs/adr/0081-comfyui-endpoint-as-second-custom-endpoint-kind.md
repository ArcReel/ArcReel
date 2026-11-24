---
status: accepted
---

# ComfyUI workflow 是一种自定义调用端点

一台 ComfyUI 服务可配置为 `discovery_format: comfyui` 的自定义供应商。一个 `kind: comfyui` 端点包含一份 API 格式的 workflow 及其节点绑定，并可挂接到对应供应商的模型上。该协议与端点双向匹配，具体属于图片还是视频类型由端点定义决定。

ComfyUI 使用节点图、素材上传和二进制取件，采用专用通道。端点存储、导入导出和市场载荷复用自定义调用端点体系。这样一份 workflow 可以挂到多台兼容机器，用户也无需扩展声明式 JSON 运行时来表达节点图。见 [ADR 0067](0067-custom-endpoint-declarative-definition.md) 和 [ADR 0082](0082-node-bindings-are-the-only-truth-of-comfyui-endpoint.md)。

- **鉴权**。反向代理的凭证位置由端点定义的 `auth` 节表达，共用 [ADR 0008](0008-custom-provider-credentials-single-field.md) 的单密钥连接模型。供应商级连通性检查只按 Bearer 探测 `/system_stats`，部署用不同鉴权模板时可用端点的测试连接验证。模型发现不适用。
- **测试**。端点测试提供预览请求和测试连接，产物路径由实现固定，无需为定义提供验证响应模式。
- **费用**。ComfyUI 供应商默认倍率为 0。用户改成非零时仍按模型目录、用量与倍率计算参考费用，缺少价格时显示未定价。执行时长另行记录。见 [倍率实现](../../lib/billing/multipliers.py) 和 [ADR 0100](0100-price-table-meters-and-dimensions.md)。
- **停止**。超时或主动放弃生成后，通道尽力通知 ComfyUI 停止本任务，减少显卡占用。远端停止失败只记录日志，本地任务仍按本地状态机结算。
