## ADDED Requirements

### Requirement: 导出端点支持 scope 参数
导出端点 `GET /api/v1/projects/{name}/export` SHALL 接受 `scope` query param，值为 `full` 或 `current`，默认为 `full`。

- `scope=full`：打包项目目录下所有文件（现有行为）
- `scope=current`：跳过 `versions/` 目录下的历史资源文件，仅保留裁剪后的 `versions/versions.json`

#### Scenario: 默认 scope 为 full
- **WHEN** 导出请求未携带 scope 参数
- **THEN** 系统以 `full` 模式打包，ZIP 包含 `versions/` 目录下的所有历史文件

#### Scenario: scope=full 导出全部数据
- **WHEN** 导出请求携带 `scope=full`
- **THEN** ZIP 包含项目目录下所有文件（含 `versions/` 完整内容），与现有行为一致

#### Scenario: scope=current 跳过历史版本文件
- **WHEN** 导出请求携带 `scope=current`
- **THEN** ZIP 不包含 `versions/storyboards/`、`versions/videos/`、`versions/characters/`、`versions/clues/` 目录下的任何文件

#### Scenario: scope 值无效
- **WHEN** 导出请求携带 `scope=invalid`
- **THEN** 系统返回 422，提示 scope 必须为 `full` 或 `current`

### Requirement: 仅当前版本导出保留裁剪后的版本元数据
当 `scope=current` 时，ZIP 中 SHALL 包含 `versions/versions.json` 文件，但内容经过裁剪：每个资源的 `versions` 数组仅保留 `current_version` 对应的那一条记录。

裁剪后的 `versions.json` 保留以下元数据：
- `current_version` 编号
- 当前版本的 `prompt`（生成 prompt）
- 当前版本的 `created_at`（创建时间）
- 当前版本的 `version` 编号

#### Scenario: 裁剪后的 versions.json 只含当前版本记录
- **WHEN** 项目 storyboard E1S01 有 3 个版本（current_version=3），以 `scope=current` 导出
- **THEN** ZIP 中 `versions/versions.json` 的 `storyboards.E1S01.versions` 数组仅包含 version 3 的记录，`current_version` 仍为 3

#### Scenario: 裁剪后的 versions.json 保留生成 prompt
- **WHEN** 以 `scope=current` 导出，当前版本有 prompt 元数据
- **THEN** 裁剪后的 `versions/versions.json` 中当前版本记录的 `prompt` 字段被保留

### Requirement: 导出清单标记 scope
`arcreel-export.json` 清单文件 SHALL 包含 `scope` 字段，值为 `"full"` 或 `"current"`，反映实际导出范围。

#### Scenario: full 导出清单 scope 为 full
- **WHEN** 以 `scope=full` 导出
- **THEN** `arcreel-export.json` 中 `scope` 字段值为 `"full"`

#### Scenario: current 导出清单 scope 为 current
- **WHEN** 以 `scope=current` 导出
- **THEN** `arcreel-export.json` 中 `scope` 字段值为 `"current"`

### Requirement: 前端导出交互支持范围选择
前端 SHALL 在用户点击导出按钮后显示选择弹窗，提供两个导出选项：

- **仅当前版本**（推荐）：标注为推荐选项，说明不含版本历史、体积更小
- **全部数据**：说明包含完整版本历史

用户选择后，前端 SHALL 依次：
1. 调用 `POST /api/v1/projects/{name}/export/token` 获取下载 token
2. 构造下载 URL：`/api/v1/projects/{name}/export?download_token=xxx&scope=yyy`
3. 通过 `window.open` 或 `<a>` 标签触发浏览器原生下载

#### Scenario: 用户选择仅当前版本导出
- **WHEN** 用户点击导出按钮并选择 "仅当前版本"
- **THEN** 浏览器发起 `scope=current` 的原生下载请求，可在浏览器下载管理器中看到下载进度

#### Scenario: 用户选择全部数据导出
- **WHEN** 用户点击导出按钮并选择 "全部数据"
- **THEN** 浏览器发起 `scope=full` 的原生下载请求

#### Scenario: 导出过程中用户可切换页面
- **WHEN** 用户触发导出下载后切换到其他页面
- **THEN** 下载不中断，因为由浏览器原生下载管理器接管

#### Scenario: 下载 token 获取失败
- **WHEN** 获取下载 token 的请求失败（网络错误或认证过期）
- **THEN** 前端显示错误 toast 提示，不触发下载
