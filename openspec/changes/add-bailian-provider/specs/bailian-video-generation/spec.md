## ADDED Requirements

### Requirement: 百炼视频后端注册与首版模型范围声明
系统 MUST 将 `wan2.7-t2v`、`wan2.7-i2v`、`wan2.7-r2v`、`wan2.7-videoedit` 注册为 `bailian` 的视频模型。默认视频模型 SHALL 为 `wan2.7-t2v`。首版实现 MUST 覆盖 `wan2.7-t2v` 与 `wan2.7-i2v`，并为其余模型保留注册信息以支持后续扩展。

#### Scenario: 默认视频模型可被识别
- **WHEN** 系统加载 `bailian` 供应商注册信息
- **THEN** 视频模型列表中 MUST 包含 `wan2.7-t2v`、`wan2.7-i2v`、`wan2.7-r2v` 与 `wan2.7-videoedit`
- **AND** `wan2.7-t2v` MUST 被标记为默认模型

#### Scenario: 首版实现模型范围受限
- **WHEN** 首版代码初始化百炼视频后端能力
- **THEN** 系统 MUST 支持 `wan2.7-t2v` 与 `wan2.7-i2v` 的生成流程
- **AND** `wan2.7-r2v` 与 `wan2.7-videoedit` MAY 保留为未实现能力等待后续扩展

### Requirement: 百炼视频后端支持异步任务生成
系统 MUST 通过 DashScope 视频生成异步接口执行视频生成，并采用“提交任务、轮询状态、下载结果”的流程返回最终视频结果。

#### Scenario: 文生视频异步任务成功完成
- **WHEN** 用户提交文生视频请求
- **THEN** 系统 MUST 调用百炼视频生成接口创建任务
- **AND** 系统 MUST 轮询任务状态直到成功或失败
- **AND** 任务成功后 MUST 下载生成视频并返回结果

#### Scenario: 图生视频异步任务成功完成
- **WHEN** 用户提交包含首帧图像的图生视频请求
- **THEN** 系统 MUST 将首帧图像作为输入发送给百炼视频接口
- **AND** 任务成功后 MUST 下载生成视频并返回结果

### Requirement: 百炼视频后端执行模型约束校验
系统 MUST 根据模型能力和文档约束，在请求发送前校验时长、输入类型与分辨率是否有效。对于不满足约束的请求，系统 MUST 在本地返回明确错误，而不是直接调用远端接口。

#### Scenario: 有效时长通过校验
- **WHEN** 用户对 `wan2.7-t2v` 提交 2 到 15 秒之间的视频请求
- **THEN** 系统 MUST 允许该请求进入远端调用流程

#### Scenario: 无效时长被本地拒绝
- **WHEN** 用户对 `wan2.7-i2v` 提交超出支持范围的视频时长
- **THEN** 系统 MUST 在调用百炼接口前返回参数校验错误

### Requirement: 百炼视频结果包含排障元数据
系统 MUST 在视频生成结果中保留 `task_id` 与 `request_id` 等排障所需元数据，以便在失败定位和用户支持场景中追踪远端任务。

#### Scenario: 成功结果包含任务标识
- **WHEN** 百炼视频生成任务成功完成
- **THEN** 返回结果 MUST 包含对应的 `task_id`
- **AND** 返回结果 MUST 在可用时包含 `request_id`
