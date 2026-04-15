## ADDED Requirements

### Requirement: 百炼图像后端注册与模型能力声明
系统 MUST 将 `wan2.7-image-pro` 与 `wan2.7-image` 注册为 `bailian` 的图像模型。默认图像模型 SHALL 为 `wan2.7-image-pro`，并声明 `text_to_image` 与 `image_to_image` 能力。

#### Scenario: 默认图像模型可被识别
- **WHEN** 系统加载 `bailian` 供应商注册信息
- **THEN** 图像模型列表中 MUST 包含 `wan2.7-image-pro` 与 `wan2.7-image`
- **AND** `wan2.7-image-pro` MUST 被标记为默认模型
- **AND** 两个模型 MUST 声明 `text_to_image` 与 `image_to_image` 能力

### Requirement: 百炼图像后端支持异步任务生成
系统 MUST 通过 DashScope 图像异步任务接口执行图像生成，并采用“提交任务、轮询状态、下载结果”的流程返回最终图片结果。

#### Scenario: 文生图异步任务成功完成
- **WHEN** 用户提交文生图请求
- **THEN** 系统 MUST 调用百炼图像生成异步接口创建任务
- **AND** 系统 MUST 轮询任务状态直到成功或失败
- **AND** 任务成功后 MUST 下载生成图片并返回结果

#### Scenario: 图生图异步任务成功完成
- **WHEN** 用户提交包含参考图的图生图请求
- **THEN** 系统 MUST 将参考图作为输入发送给百炼图像接口
- **AND** 任务成功后 MUST 下载生成图片并返回结果

### Requirement: 百炼图像后端执行分辨率映射
系统 MUST 将 ArcReel 侧的 `aspect_ratio` 映射为 DashScope 图像接口要求的 `size` 参数，并在请求发送前完成转换。

#### Scenario: 宽屏比例映射为百炼尺寸
- **WHEN** 用户请求 `16:9` 比例的图像生成
- **THEN** 系统 MUST 将请求转换为百炼支持的 `1280*720` 尺寸参数

#### Scenario: 竖屏比例映射为百炼尺寸
- **WHEN** 用户请求 `9:16` 比例的图像生成
- **THEN** 系统 MUST 将请求转换为百炼支持的 `720*1280` 尺寸参数

### Requirement: 百炼图像后端支持首版核心参数与参考图输入
系统 MUST 在首版支持 `prompt`、生成数量、`watermark`、`aspect_ratio` 映射以及最多 3 张参考图输入。系统 MAY 在后续版本扩展 `negative_prompt`、`seed` 等高级参数，但首版 MUST 保证核心参数可用。

#### Scenario: 核心参数用于生成请求
- **WHEN** 用户提交包含 prompt、比例、生成数量和 watermark 的图像生成请求
- **THEN** 系统 MUST 将这些核心参数映射为百炼图像接口请求字段

#### Scenario: 多参考图输入受支持
- **WHEN** 用户提交 1 到 3 张参考图进行图生图请求
- **THEN** 系统 MUST 接受这些参考图并发送给百炼图像接口
