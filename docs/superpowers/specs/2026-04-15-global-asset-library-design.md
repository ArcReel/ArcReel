# 全局资产库 + Clue 重构设计

## 背景

ArcReel 当前项目之间互相隔离，同一用户反复创作相似世界观时（如古风短剧系列），角色 / 线索需要重复录入，无法跨项目复用。本设计引入**全局资产库**（可跨项目复用的人物 / 场景 / 道具仓库），并借此机会**彻底重构旧 `clue` 概念**：线索拆分为独立的 `scene`（场景）和 `prop`（道具）两类一级对象，删除 `importance` 字段。

同时修复项目工作台中几处 UI 痛点（手动新增按钮位置、非模态表单、空态无法进入页面）。

## 目标与范围

### 目标

1. **资产库（新功能）**：全局单一资产库，数据库存储；支持新增 / 编辑 / 删除 / 搜索 / 按类型浏览；跨项目复用以"快照复制"为语义，不做引用耦合。
2. **Clue 彻底重构**：删除 `clue` / `importance` 概念，拆为独立的 `scene` / `prop` 两类一级对象，覆盖后端数据模型、路由、文件目录、agent skill、i18n 文案。所有 scene / prop 一律视为"需要设计图"（等价旧 major）。
3. **UI 改造**：三类资源页面（角色 / 场景 / 道具）顶部加统一操作栏，新增 / 编辑表单改为模态，空态也可操作；侧边栏空态按钮可点击。

### 非目标

- 多用户 / 权限 / 分享（ArcReel 当前单用户）
- 资产版本历史（资产库只保留最新态，旧版本不保留）
- 软删 / 回收站（硬删 + 物理删图）
- 分镜图（SegmentCard）加入资产库（分镜是叙事产物，不属于跨项目可复用资产）
- 标签系统 / 风格 palette 等扩展字段
- 资产评论 / 协作

## 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 资产库作用域 | 全局单一库 | 当前无多用户 |
| 资产库与项目耦合 | 快照复制（双向独立） | 保持项目自治，导出 ZIP 不依赖资产库 |
| Clue 处理 | 彻底重构为独立 scene + prop | 去除概念混淆；符合"去掉线索这个词"的需求 |
| Importance 字段 | 删除，所有视为 major | 查证后 importance 仅影响"是否入自动生成队列"，删除后以"缺 sheet 即待生成"判定 |
| 项目内结构 | 独立一级对象（分目录、分路由） | 与重构方向一致；scenes 与 props 各自完整体系 |
| 数据存储 | Asset ORM 表 + 图片独立目录 | 全局查询 / 搜索 / 分页的典型场景；沿用现有 Async ORM 骨架 |
| 资产字段 | 最小保真集（含 voice_style for character） | 与来源对齐、roundtrip 不丢字段 |
| 图片必填 | 否（可选） | 允许先占坑、后补图 |
| name 唯一性 | type 内唯一 | `UNIQUE(type, name)` 约束 |
| 重名冲突 | 弹窗交用户决定（覆盖 / 改名 / 取消） | 不静默合并也不静默拒绝 |
| 资产可变更 | 完整 CRUD + 硬删 | 用户会想补描述 / 换图 / 改名；软删会膨胀 |
| 加入资产库流程 | 预览 + 确认模态 | 给改名 / 改描述机会，避免误操作 |
| 资产库入口 | GlobalHeader 图标按钮 | 全局可达，不挤占大厅布局 |
| 资产库主页布局 | 顶部 Tab + 网格 | 与项目工作台交互风格一致 |
| 项目工作台操作栏 | 标题 + 计数 + 搜索 + 两按钮右对齐 | 空态也可交互，解决原"无法进入空页"问题 |
| 挑选对话框形态 | 居中大模态，多选，入口锁类型 | 可见图预览 + 批量导入；tab 冗余已移除 |
| 卡片添加按钮 | 顶部图标按钮行（与生成 / 编辑 / 版本同级） | 用户选择 A 方案（曝光高）；后续可根据使用情况收缩到三点菜单 |
| 资产编辑模态 | 统一的 `AssetFormModal`，5 场景复用 | 单组件、按 type / mode / scope 参数化 |

## 数据模型

### Asset ORM (`lib/db/models/asset.py`)

```python
class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[str]            # uuid4 字符串主键
    type: Mapped[str]          # 'character' | 'scene' | 'prop'
    name: Mapped[str]          # type 内唯一（UniqueConstraint(type, name)）
    description: Mapped[str]   # 可空
    voice_style: Mapped[str]   # 仅 character 使用，其它 type 留空串
    image_path: Mapped[str]    # 相对路径：'_global_assets/<type>/<uuid>.<ext>'；可空
    source_project: Mapped[str | None]  # 入库来源项目名；手动新增为 None
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    __table_args__ = (UniqueConstraint("type", "name", name="uq_asset_type_name"),)
```

配套 Repository：`lib/db/repositories/asset_repo.py`，异步方法：
- `list(type: str | None, q: str | None, limit: int, offset: int)`
- `get_by_id(id)` / `get_by_type_name(type, name)`
- `create(...)` / `update(id, ...)` / `delete(id)`
- `exists(type, name)` — 冲突检测

### project.json schema 变更

**删除**：顶层 `clues`；clue 内部 `importance`、`type`、`clue_sheet` 字段。
**新增**：顶层 `scenes`、`props` 两个字典；`schema_version` 版本字段。

```jsonc
// before
{
  "name": "demo",
  "clues": {
    "玉佩": { "type": "prop", "importance": "major", "description": "...", "clue_sheet": "clues/玉佩.png" },
    "庙宇": { "type": "location", "importance": "minor", "description": "..." }
  }
}
// after
{
  "name": "demo",
  "schema_version": 1,
  "scenes": {
    "庙宇": { "description": "...", "scene_sheet": "scenes/庙宇.png" }
  },
  "props": {
    "玉佩": { "description": "...", "prop_sheet": "props/玉佩.png" }
  }
}
```

剧本 JSON（`scripts/*.json`）中：
- `DramaScene.clues: list[str]` → `scenes: list[str]` + `props: list[str]`
- `NarrationSegment.clues: list[str]` → `scenes: list[str]` + `props: list[str]`
- root 补 `schema_version: 1`

### 文件系统变更

```
projects/
  _global_assets/              # 新增：全局资产库图片
    character/<uuid>.<ext>
    scene/<uuid>.<ext>
    prop/<uuid>.<ext>
  {project}/
    characters/                # 不变
    scenes/                    # 新增，取代 clues/ 中 type=location 部分
    props/                     # 新增，取代 clues/ 中 type=prop 部分
    versions/
      characters/              # 不变
      scenes/                  # 新增
      props/                   # 新增
    scripts/                   # 剧本 JSON 内部字段迁移
```

`ProjectManager.list_projects()` 显式过滤下划线前缀目录（保留现有约定）。

### Alembic 迁移（DB schema）

新增一条迁移：
- `CREATE TABLE assets (...)` + `UNIQUE(type, name)` + 索引 `(type)`, `(name)`

### 自动迁移机制（文件级 schema）

**版本策略**：
- `CURRENT_SCHEMA_VERSION = 1`
- 缺失 `schema_version` 字段视为 v0，需迁移到 v1
- 未来再升级直接在 MIGRATORS 中注册 `2`、`3`……

**骨架** (`lib/project_migrations/`)：
```
lib/project_migrations/
  __init__.py          # CURRENT_SCHEMA_VERSION = 1, MIGRATORS = {0: migrate_v0_to_v1}
  v0_to_v1_clues_to_scenes_props.py   # 纯函数，幂等
```

**启动流程**（`server/app.py` 的 startup event）：
1. 执行 `alembic upgrade head`（DB schema）
2. 扫 `projects/` 每个项目目录（跳过 `_global_assets/`、`.arcreel.db` 等）
3. 读取 `project.json.schema_version`（缺失视为 0）
4. 当 `< CURRENT_SCHEMA_VERSION`：
   - 迁移前复制备份：`project.json.bak.v{from}-{timestamp}`
   - 按版本顺序逐级跑 migrator
   - 原子写回（tmpfile + `os.replace`）
   - 同时扫 `{project}/scripts/*.json` 级联迁移剧本
5. **备份清理**：扫 `projects/*/project.json.bak.*`，时间戳超过 7 天的自动删除
6. **错误隔离**：单项目迁移失败 → 记 `projects/_migration_errors.log` + `server.log`，**不中断启动**；大厅用 "migration_failed" 状态标记，禁用项目卡片直到用户介入

**v0 → v1 迁移内容**：
- project.json：`clues.*.type=="location"` → `scenes{name}`；`clues.*.type=="prop"` → `props{name}`；删除 `importance`、`type`、`clue_sheet` 字段，改为 `scene_sheet` 或 `prop_sheet`；补 `schema_version: 1`
- 文件系统：`{project}/clues/` 下所有 PNG 按原 clue.type 分流到 `{project}/scenes/` 或 `{project}/props/`，同步重命名
- 剧本 JSON：`clues: []` 按引用名在原 project.json 中查 type，拆分为 `scenes: []` + `props: []`
- 版本目录：`{project}/versions/clues/` → `versions/scenes/` 或 `versions/props/`（按原 clue 所属分流）
- DB tasks 表：历史 `task_type='clue'` 记录保留原值（只读展示），新任务只发 `scene` / `prop`

**幂等保证**：同项目再次跑迁移，检测到 `schema_version >= 1` 即 no-op。

## 后端 API

### 资产库路由（新增）`server/routers/assets.py` → `/api/v1/assets/*`

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/assets` | 列表；参数 `?type=character\|scene\|prop`、`?q=<name 模糊>`、`?limit=`、`?offset=` |
| `GET` | `/assets/{id}` | 详情 |
| `POST` | `/assets` | 手动新增：`multipart/form-data`，表单 `type, name, description?, voice_style?` + 可选 image 文件 |
| `POST` | `/assets/from-project` | 从项目入库：`{project_name, resource_type, resource_id, override_name?, overwrite?}`；后端负责图片复制与冲突检查；409 返回已有资产摘要 |
| `PATCH` | `/assets/{id}` | 编辑 name / description / voice_style |
| `POST` | `/assets/{id}/image` | 单独替换图片 |
| `DELETE` | `/assets/{id}` | 硬删 + 物理删图 |
| `POST` | `/assets/apply-to-project` | 批量应用到项目：`{asset_ids: [...], target_project, conflict_policy: 'skip'\|'overwrite'\|'rename'}`；返回逐条 `{succeeded: [...], failed: [{id, reason}]}` |

### 静态文件服务

`server/routers/files.py`：
- 保留 `GET /files/{project_name}/{path:path}`（项目内）— 不变
- 新增 `GET /global-assets/{type}/{filename}` — 专供资产库图片

### Scene / Prop 路由（取代 Clue）

- 删除 `server/routers/clues.py`
- 新增 `server/routers/scenes.py` → `/api/v1/scenes/*`：`POST /{project}/add`、`PATCH /{project}/{name}`、`DELETE /{project}/{name}`
- 新增 `server/routers/props.py` → `/api/v1/props/*`：同构
- `server/routers/generate.py`：`POST /generate/clue` → 拆分 `POST /generate/scene` + `POST /generate/prop`

### i18n

- 后端新增 `lib/i18n/{zh,en}/assets.py`（资产库错误 / 提示）
- `lib/i18n/{zh,en}/errors.py` 删除 `invalid_importance` / `invalid_clue_type`

## 图片与文件流

### 全局目录约定

根路径 `projects/_global_assets/`，按 type 分三个子目录。命名 `<uuid>.<原扩展>`，uuid4 生成。

### 复制语义（始终物理复制，不引用）

| 动作 | 源 | 目标 |
|------|-----|------|
| 入库（from-project） | `{project}/characters/王小明.png` | `_global_assets/character/{uuid}.png` |
| 手动上传 | multipart 上传的文件 | `_global_assets/{type}/{uuid}.{ext}` |
| 替换图片 | multipart 上传 | 覆盖同 uuid 文件；更新 `updated_at` |
| 应用到项目 | `_global_assets/{type}/{uuid}.png` | `{project}/{type}s/{new_name}.png` |

### URL 与 fingerprint

- 前端 `API.getFileUrl(project, path, fp)` — 项目内图片（不变）
- 前端 `API.getGlobalAssetUrl(assetId, fp)` → `/api/v1/global-assets/{type}/{filename}?fp={updated_at_ts}` — 新增
- `projectsStore.getAssetFingerprint(path)` — 不变；新增 `getGlobalAssetFingerprint(assetId)` 使用 Asset.updated_at

### 约束

- 接受格式：`.png / .jpg / .jpeg / .webp`
- 单文件上限：5 MB（沿用现有 source file 限制）
- 不做转码，保留原扩展

## 前端组件与路由

### 新增路由

`frontend/src/router.tsx`：
- `/app/assets` — 资产库主页（默认 character Tab）
- URL 参数 `?type=character|scene|prop` 控制 Tab

GlobalHeader 新增 📦 图标按钮，`navigate("/app/assets")`。

### 新增文件

```
frontend/src/
  components/pages/
    AssetLibraryPage.tsx         # 顶部 Tab + 搜索 + 网格 + 新增按钮
  components/assets/
    AssetGrid.tsx                # 卡片网格，懒加载图
    AssetCard.tsx                # 图 + 名称 + 描述 + hover 动作（编辑 / 删除 / 应用到项目）
    AssetFormModal.tsx           # 统一 create/edit/import 模态（5 场景复用）
    AssetPickerModal.tsx         # 【从资产库选择】对话框，锁 type 多选
    AddToLibraryButton.tsx       # 卡片右上 📦 图标按钮，点击 → AssetFormModal(import)
    GalleryToolbar.tsx           # 角色 / 场景 / 道具页顶部统一操作栏
  stores/
    assets-store.ts              # zustand：list / byId / loading / actions
  types/
    asset.ts                     # Asset, AssetType, AssetPayload 等
  i18n/{zh,en}/
    assets.ts                    # 新 namespace
```

### 改造文件

| 文件 | 改动 |
|------|------|
| `router.tsx` | 加 `/app/assets` |
| `layout/GlobalHeader.tsx` | 加 📦 按钮 |
| `layout/AssetSidebar.tsx` | 空态文本改可点击按钮；Clues 子节拆 Scenes + Props 两节 |
| `canvas/lorebook/LorebookGallery.tsx` | 拆解：角色 / 场景 / 道具各独立页 |
| `canvas/lorebook/CharacterCard.tsx` | 顶部图标行加 📦；现有 inline 编辑保留 |
| `canvas/lorebook/ClueCard.tsx` | 删除 |
| `canvas/lorebook/AddClueForm.tsx` | 删除 |
| `canvas/lorebook/AddCharacterForm.tsx` | 删除（行为迁入 AssetFormModal） |
| `canvas/lorebook/` | 新增 `SceneCard.tsx`、`PropCard.tsx`（字段无 type/importance） |
| `canvas/StudioCanvasRouter.tsx` | 路由 `/characters` `/scenes` `/props` 三条；每页渲染 `GalleryToolbar` + 对应卡片网格；表单统一走 AssetFormModal |
| `types/project.ts` | `Clue` 删除；新增 `Scene`、`Prop`；`Character` 不变 |
| `types/script.ts` | `DramaScene.clues` / `NarrationSegment.clues` 拆 `scenes` + `props` |
| `stores/projects-store.ts` | 字段迁移；fingerprint 兼容新路径 |
| `api.ts` | 删 addClue/updateClue/generateClue/deleteClue；新增 scene/prop/assets 系列 |
| `i18n/{zh,en}/dashboard.ts` | 删 importance / clue 文案；新增 scene / prop / asset_library 文案 |

### `AssetFormModal` API

```ts
interface AssetFormModalProps {
  type: "character" | "scene" | "prop";
  mode: "create" | "edit" | "import";
  initialData?: Partial<Asset>;
  scope: "project" | "library";   // project 写入当前项目、library 写入资产库
  conflictWith?: Asset;            // import 模式下同名冲突时传入，用于展示警告
  targetProject?: string;          // scope=project 时必传
  onClose: () => void;
  onSubmit: (payload: AssetPayload) => Promise<void>;
}
```

5 个触发场景映射：

| 场景 | 组合 |
|------|------|
| 资产库 + 新增 | scope=library, mode=create |
| 资产库卡片编辑 | scope=library, mode=edit, initialData=asset |
| 项目 + 新增角色 / 场景 / 道具 | scope=project, mode=create, targetProject=current |
| 项目卡片编辑（改造） | scope=project, mode=edit, initialData=resource |
| 项目 📦 加入资产库 | scope=library, mode=import, initialData=项目数据, conflictWith=(查询结果) |

## Agent / Skill / Prompt 改造

### 统一 `generate-assets` skill

- 合并原 `.claude/skills/generate-clues/` + 任何 `generate-characters/` 逻辑（如存在）为 `.claude/skills/generate-assets/`
- 入参：`--type=character|scene|prop`；未指定时扫所有 pending（缺 sheet 的资源）自动按类分发
- SKILL.md 写明：三类资产可并行调度；pending 判定从"`importance==major` 且缺 sheet"改为"缺对应 sheet"

### CLI 脚本统一

- `.claude/skills/manage-project/scripts/add_characters_clues.py` → `add_assets.py`
- 入参分段：`--characters` / `--scenes` / `--props`（三段 JSON），去掉 `--clues`
- 字段 schema：去 `type` / `importance`

### Agent 重命名

- `.claude/agents/analyze-characters-clues.md` → `analyze-assets.md`
- 输出 schema：`{characters: {...}, scenes: {...}, props: {...}}` 三段
- Prompt 内容更新：不再要求标注 `importance`，不再使用"线索"一词

### manga-workflow 阶段合并

- 原阶段 5（角色设计）与阶段 6（线索设计）合并为**单阶段「角色 / 场景 / 道具设计」**
- 三个子任务并行调度，触发条件"任一类资产缺 sheet"
- SKILL.md 流程图同步

### Backend prompt builders

| 文件 | 改动 |
|------|------|
| `lib/prompt_builders.py` | `build_clue_prompt` → 拆 `build_scene_prompt` + `build_prop_prompt` |
| `lib/prompt_builders_script.py` | 剧本生成 prompt 中"线索提取"改为"场景 / 道具提取"，按语义分类产出 |
| `lib/data_validator.py` | 删 `VALID_CLUE_IMPORTANCE`；校验 scenes / props dict；删 importance 相关校验 |
| `lib/script_models.py` | `DramaScene.clues` / `NarrationSegment.clues` 拆两段 |
| `lib/project_manager.py` | `add_clue` / `update_clue` / `get_pending_clues` / `add_clues_batch` 拆两套；去掉 importance 参数 |
| `lib/status_calculator.py` | `clues_count` 拆 `scenes_count` + `props_count`；项目完成度算法同步 |

### 其他耦合

| 模块 | 改动 |
|------|------|
| `server/services/generation_tasks.py` | `execute_clue_task` 拆 `execute_scene_task` + `execute_prop_task`；`collect_reference_sheets` 中 `clue_field` 拆 `scene_field` + `prop_field`；分镜参考图注入两路 sheet |
| `server/services/project_events.py` | 所有 `clue*` 事件 name 改 scene / prop；前端 `useProjectEventsSSE` 同步 |
| `server/services/project_archive.py` | 导出 ZIP 时打包 `scenes/` 和 `props/` 目录 |
| `server/services/cost_estimation.py` | 费用预估按 scene + prop 分别累加 |
| `server/routers/versions.py` | 资源类型字符串 `"clues"` 拆 `"scenes"` + `"props"`；版本目录命名同步 |

## 错误处理与冲突策略

| 场景 | 策略 |
|------|------|
| `(type, name)` 唯一冲突 | DB `UNIQUE` 约束；应用层抛 409；前端弹冲突模态（覆盖 / 改名 / 取消） |
| 上传图片失败（IO / 磁盘） | 事务回滚，asset 记录不创建；返 500 + i18n 错误 |
| 删资产但图片缺失 | DB 条目正常删除，warning 日志，不阻塞 |
| 迁移中途失败 | 单项目隔离；写 `_migration_errors.log`；不中断启动；大厅显示 "migration_failed" 禁用 |
| 自动迁移遇到损坏 project.json | 跳过 + 日志；不尝试修复 |
| 批量 `apply-to-project` 部分失败 | 逐条处理；返回 `{succeeded, failed[{id, reason}]}`；前端明细展示 |
| `from-project` 角色无 `character_sheet` | 允许入库，`image_path=None`；UI 占位图 + 引导上传 |
| 资产库 → 项目的目标项目同名冲突 | 弹窗逐批处理（同上冲突模态） |
| SSE 事件 clue* 字段 | 后端发送端一次性切换；无"双发兼容期" |

## 测试与验收

### 后端测试

- **新增**：`test_assets_router.py` / `test_assets_repo.py` / `test_project_migrations.py` / `test_scenes_router.py` / `test_props_router.py`
- **删除**：`test_clues_router.py`
- **改**：`test_project_manager_more.py` / `test_status_calculator.py` / `test_data_validator.py` / `test_generation_tasks_service.py` / `test_project_archive_service.py` / `test_project_events_service.py` / `test_versions_router.py` / `test_generate_router.py` / `test_files_router.py`

覆盖率目标：维持 ≥80%（CI 现有门槛）。

### 前端测试

- **新增**：`AssetFormModal.test.tsx` / `AssetPickerModal.test.tsx` / `AssetLibraryPage.test.tsx`
- **改**：`StudioCanvasRouter.test.tsx` / `api.test.ts` / `useProjectEventsSSE.test.tsx`

i18n 一致性：`test_i18n_consistency.py` 自动覆盖新增 key。

### 迁移测试

- v0 → v1 结构正确、文件分流正确
- 再跑一次 v0 → v1 幂等（schema_version=1 时直接 no-op）
- 损坏 project.json 隔离，其他项目正常启动
- 备份 7 天后清理（时间戳解析正确）

### 手工验收清单

**资产库**：
- [ ] GlobalHeader 📦 点击进入 `/app/assets`，三 Tab + 搜索正常
- [ ] 新建三类资产：填名 / 描述 / 上传图，列表出现
- [ ] 编辑资产：改名 / 改描述 / 换图 / 删图
- [ ] 删除资产：硬删 + 图片文件消失
- [ ] name 模糊搜索

**项目 → 资产库**：
- [ ] 角色 / 场景 / 道具卡片 📦 点击弹预览模态
- [ ] 同名冲突弹改名 & 覆盖选项
- [ ] 入库后资产库出现条目，源项目数据不变

**资产库 → 项目**：
- [ ] 项目内【从资产库选择】弹大模态，锁类型
- [ ] 多选 + 重名已置灰
- [ ] 导入后项目出现对应资产卡

**UI 改造**：
- [ ] 三类页顶部操作栏显示，空态按钮可点
- [ ] 新增 / 编辑都走模态
- [ ] 侧边栏空态入口可点击

**Clue 重构**：
- [ ] 启动迁移一个老项目：`clues/` 消失，`scenes/` + `props/` 正确分流
- [ ] 老剧本 `clues[]` 拆 `scenes[]` + `props[]`
- [ ] 新路径生成场景 / 道具图
- [ ] 分镜参考图正确注入两路 sheet
- [ ] 导出 ZIP 包含新目录

### 性能 / 容量

- 资产库 ≤ 10K 条目查询响应可接受（索引 + 分页）
- 单项目 `clues` ≤ 1000 条迁移应在 5 秒内完成
- `_global_assets/` 磁盘占用按上传量线性增长，无额外放大

## 开放问题

（无，设计已全部定稿）

## 参考

- 现有 ORM 模型：`lib/db/models/api_call.py`、`lib/db/models/task.py`
- 现有 Repository 模式：`lib/db/repositories/credential_repo.py`
- 现有 i18n 范式：`lib/i18n/zh/errors.py`、`frontend/src/i18n/zh/dashboard.ts`
- 现有文件服务：`server/routers/files.py`
- 现有项目管理：`lib/project_manager.py`
- 现有 agent skill：`agent_runtime_profile/.claude/skills/generate-clues/SKILL.md`
