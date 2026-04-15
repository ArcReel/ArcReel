## ADDED Requirements

### Requirement: 百炼文本后端注册与模型能力声明
系统 MUST 将 `bailian` 注册为可用的文本供应商，并为 `qwen3.6-plus`、`qwen-max`、`qwen-plus`、`qwen-turbo` 声明对应的文本模型能力。默认文本模型 SHALL 为 `qwen3.6-plus`，并声明 `text_generation`、`structured_output` 与 `vision` 能力。

#### Scenario: 默认文本模型可被识别
- **WHEN** 系统加载 `bailian` 供应商注册信息
- **THEN** 文本模型列表中 MUST 包含 `qwen3.6-plus`
- **AND** `qwen3.6-plus` MUST 被标记为默认模型
- **AND** 该模型 MUST 声明 `text_generation`、`structured_output` 与 `vision` 能力

### Requirement: 百炼文本后端支持 OpenAI 兼容调用
系统 MUST 通过 DashScope OpenAI 兼容端点执行百炼文本生成请求，并复用现有文本后端请求结构。后端 SHALL 使用 `base_url` 配置或默认端点 `https://dashscope.aliyuncs.com/compatible-mode/v1` 初始化客户端。

#### Scenario: 使用默认兼容端点发起文本请求
- **WHEN** 用户已配置 `bailian` 的 `api_key` 且未配置 `base_url`
- **THEN** 系统 MUST 使用默认兼容端点初始化文本客户端
- **AND** 文本生成请求 MUST 发送到 DashScope OpenAI 兼容接口

#### Scenario: 使用自定义 base_url 发起文本请求
- **WHEN** 用户为 `bailian` 配置了自定义 `base_url`
- **THEN** 系统 MUST 使用该 `base_url` 初始化文本客户端

### Requirement: 百炼文本后端支持结构化输出降级
系统 MUST 支持结构化输出请求。当原生 `response_format` 调用因 schema 兼容性失败时，系统 MUST 降级到兼容的结构化输出路径，而不是直接返回失败。

#### Scenario: 原生 schema 调用成功
- **WHEN** 用户请求结构化输出且 DashScope 兼容接口接受该 schema
- **THEN** 系统 MUST 返回符合 schema 的结构化结果

#### Scenario: 原生 schema 调用失败时降级
- **WHEN** 用户请求结构化输出且 DashScope 兼容接口返回 schema 不兼容错误
- **THEN** 系统 MUST 自动切换到降级路径继续完成生成
- **AND** 系统 MUST 不因该类兼容性错误直接终止请求

### Requirement: 百炼文本后端支持视觉输入
系统 MUST 支持带图像输入的文本生成请求，并将视觉输入转换为 DashScope 兼容接口可接受的消息格式。

#### Scenario: 视觉输入参与文本生成
- **WHEN** 用户提交包含图像与文本的文本生成请求
- **THEN** 系统 MUST 将图像和文本一并构造为兼容接口消息
- **AND** 模型响应 MUST 作为文本生成结果返回
