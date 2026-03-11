## ADDED Requirements

### Requirement: 顶栏四 Tab 结构

配置页 SHALL 将顶栏 Tab 从原有的 `[config, api-keys]` 扩展为四个 Tab：**ArcReel 智能体配置**（agent）、**AI 生图/生视频配置**（media）、**高级配置**（advanced）、**API Keys**（api-keys）。

各 Tab 承载内容：
- **ArcReel 智能体配置**：Anthropic API Key、Base URL、各模型选择字段
- **AI 生图/生视频配置**：Gemini API Key、Base URL、后端选择、模型选择、Vertex 凭证等
- **高级配置**：速率限制（RPM）、请求间隔、最大并发 Worker 数
- **API Keys**：现有 API Key 管理功能（不变）

#### Scenario: 用户打开系统配置页
- **WHEN** 用户导航至 `/app/settings`
- **THEN** 页面 SHALL 显示四个顶栏 Tab，默认激活第一个 Tab（ArcReel 智能体配置）

#### Scenario: Tab 顺序
- **WHEN** 页面渲染时
- **THEN** Tab 顺序 SHALL 固定为：ArcReel 智能体配置 → AI 生图/生视频配置 → 高级配置 → API Keys

---

### Requirement: Tab 级独立保存

每个配置 Tab（agent / media / advanced）SHALL 提供独立的保存操作，一次保存该 Tab 内所有已修改字段，不影响其他 Tab。

#### Scenario: 用户在 Tab 内修改字段后保存
- **WHEN** 用户修改某配置 Tab 内的任意字段并点击该 Tab 的保存按钮
- **THEN** 系统 SHALL PATCH 该 Tab 内所有已修改字段，保存成功后 Tab 恢复为未修改状态

#### Scenario: Tab 保存中状态
- **WHEN** Tab 保存请求正在进行
- **THEN** Tab 内所有输入框 SHALL 禁用，保存按钮显示加载态，防止重复提交

#### Scenario: Tab 保存失败
- **WHEN** 保存请求返回错误
- **THEN** 系统 SHALL 在保存按钮旁显示错误提示，字段值保持用户编辑的内容

#### Scenario: Tab 无未保存变更时
- **WHEN** Tab 内所有字段值与当前已保存值相同
- **THEN** 保存按钮 SHALL 处于禁用状态

---

### Requirement: 未保存变更感知 — Sticky 保存页脚 + Tab 徽标

当配置 Tab 内存在未保存变更时，该 Tab 的保存页脚 SHALL 以 sticky 方式固定在视口底部，Tab 标签旁 SHALL 显示圆点徽标，确保用户随时可察觉并触发保存。

#### Scenario: Tab 内存在未保存变更
- **WHEN** 用户修改了当前配置 Tab 内的任意字段值
- **THEN** 该 Tab 的保存页脚 SHALL 变为 sticky 固定在屏幕底部，保存按钮 SHALL 高亮显示（primary 色调），Tab 标签旁 SHALL 显示圆点徽标（●）

#### Scenario: Tab 无未保存变更
- **WHEN** Tab 内所有字段值与已保存值相同
- **THEN** 保存页脚 SHALL 正常渲染在 Tab 内容底部（非 sticky），保存按钮 SHALL 为禁用态

#### Scenario: Tab 保存成功后
- **WHEN** 保存请求成功完成
- **THEN** sticky 状态 SHALL 解除，保存页脚回到 Tab 内容底部，Tab 标签徽标消失

#### Scenario: 用户撤销变更
- **WHEN** 用户点击保存页脚的"撤销"按钮
- **THEN** Tab 内所有字段值 SHALL 恢复为上次成功保存的值，sticky 状态解除

#### Scenario: 用户切换到其他 Tab（含未保存变更）
- **WHEN** 用户点击另一个 Tab，但当前 Tab 有未保存变更
- **THEN** 系统 SHALL 允许切换，原 Tab 标签上的圆点徽标 SHALL 持续显示，提醒用户该 Tab 有未保存变更

---

### Requirement: 所有可选字段统一提供清除按钮

所有非必填配置字段（包括 base_url、API key 等可选项）SHALL 在有值时显示清除（×）按钮，点击后立即清空字段值并触发 Tab 的未保存状态。

#### Scenario: 用户清除字段值
- **WHEN** 可选字段有值，用户点击清除按钮
- **THEN** 字段值 SHALL 立即清空，清除按钮消失，Tab 进入已修改状态（保存页脚变 sticky）

#### Scenario: 字段为空时
- **WHEN** 可选字段值为空
- **THEN** 清除按钮 SHALL 不显示

---

### Requirement: 必填配置缺失时全局入口警告

当系统必填配置未完整设置时，所有通往设置页的入口 SHALL 以红色圆点徽标标记，提醒用户进入设置完成配置。

**必填配置定义**：以下三项均需满足，系统才能正常运行：
1. ArcReel 智能体 API Key（`anthropic_api_key.is_set`）
2. AI 生图后端凭证：若 `image_backend = "aistudio"` 则 `gemini_api_key.is_set`；若 `"vertex"` 则 `vertex_credentials.is_set`
3. AI 生视频后端凭证：若 `video_backend = "aistudio"` 则 `gemini_api_key.is_set`；若 `"vertex"` 则 `vertex_credentials.is_set`

#### Scenario: 必填配置不完整时进入项目大厅
- **WHEN** 用户登录后进入项目大厅，且系统必填配置不完整
- **THEN** 项目大厅右上角的设置图标按钮 SHALL 显示红色圆点徽标

#### Scenario: 必填配置不完整时在工作区内
- **WHEN** 用户在任意项目工作区内，且系统必填配置不完整
- **THEN** 全局 Header 右上角的设置图标按钮 SHALL 显示红色圆点徽标

#### Scenario: 必填配置已完整时
- **WHEN** 系统必填配置均已配置
- **THEN** 设置图标按钮 SHALL 不显示任何徽标，与正常状态一致

#### Scenario: 用户完成配置保存后
- **WHEN** 用户在设置页成功保存了缺失的必填字段
- **THEN** 全局入口徽标 SHALL 实时消失，无需刷新页面

---

### Requirement: 必填配置缺失时设置页内警告

当系统必填配置不完整时，设置页 SHALL 在 Tab 导航上方显示警告横幅，逐条列出缺失原因，并提供快捷跳转到对应 Tab 的链接。

#### Scenario: Anthropic API Key 未配置
- **WHEN** 用户进入设置页，且 `anthropic_api_key.is_set === false`
- **THEN** 警告横幅 SHALL 包含一条"ArcReel 智能体 API Key（Anthropic）未配置"，并链接到"ArcReel 智能体配置" Tab

#### Scenario: AI 生图后端凭证未配置（AI Studio）
- **WHEN** `image_backend = "aistudio"` 且 `gemini_api_key.is_set === false`
- **THEN** 警告横幅 SHALL 包含一条"AI 生图 API Key（Gemini AI Studio）未配置"，并链接到"AI 生图/生视频配置" Tab

#### Scenario: AI 生图后端凭证未配置（Vertex）
- **WHEN** `image_backend = "vertex"` 且 `vertex_credentials.is_set === false`
- **THEN** 警告横幅 SHALL 包含一条"AI 生图 Vertex AI 凭证未上传"，并链接到"AI 生图/生视频配置" Tab

#### Scenario: AI 生视频后端凭证未配置（AI Studio）
- **WHEN** `video_backend = "aistudio"` 且 `gemini_api_key.is_set === false`
- **THEN** 警告横幅 SHALL 包含一条"AI 生视频 API Key（Gemini AI Studio）未配置"，并链接到"AI 生图/生视频配置" Tab

#### Scenario: AI 生视频后端凭证未配置（Vertex）
- **WHEN** `video_backend = "vertex"` 且 `vertex_credentials.is_set === false`
- **THEN** 警告横幅 SHALL 包含一条"AI 生视频 Vertex AI 凭证未上传"，并链接到"AI 生图/生视频配置" Tab

#### Scenario: 相同缺失原因去重
- **WHEN** `image_backend` 与 `video_backend` 使用同一提供商且该提供商凭证均未配置
- **THEN** 警告横幅 SHALL 合并为一条（如"AI 生图/生视频 API Key（Gemini AI Studio）未配置"），不重复展示

#### Scenario: 所有必填配置均已完成
- **WHEN** 系统必填配置均已配置
- **THEN** 设置页 SHALL 不显示警告横幅

---

### Requirement: Tab 草稿状态隔离

页面 SHALL 为每个配置 Tab 维护独立的草稿状态，Tab 间状态相互隔离，切换 Tab 不重置其他 Tab 的未保存变更。

#### Scenario: 同时在多个 Tab 修改字段
- **WHEN** 用户在多个 Tab 中各自进行了修改（均未保存）
- **THEN** 每个有变更的 Tab 标签 SHALL 显示圆点徽标，各 Tab 草稿状态独立保留

#### Scenario: 页面初始加载
- **WHEN** 配置数据从 API 加载完成
- **THEN** 所有 Tab SHALL 处于无未保存变更状态，保存页脚为非 sticky 禁用态
