## 1. 新增脚本开发

- [x] 1.1 创建 `add_characters_clues.py` 脚本：封装 `ProjectManager.add_characters_batch()` + `add_clues_batch()` + `validate_project()`，支持通过命令行参数或 stdin 接收 JSON 格式的角色/线索数据
- [x] 1.2 创建 `normalize_drama_script.py` 脚本：使用 `gemini-3.1-pro-preview` 模型读取 source/ 小说原文，生成 Markdown 格式的规范化剧本（`step1_normalized_script.md`）和镜头预算（`step2_shot_budget.md`），输出格式须与 `ScriptGenerator.build_drama_prompt()` 兼容
- [x] 1.3 在 `settings.json` 的 `permissions.allow` 中添加两个新脚本的 Bash 执行权限
- [x] 1.4 验证 `generate-script` 对两种 content_mode 的兼容性（narration 读 step1_segments.md、drama 读 step1_normalized_script.md），修复发现的问题

## 2. 聚焦 Subagent 创建

- [x] 2.1 提供 `analyze-characters-clues` agent 的 description，用户通过 `/agents` 命令创建（全局角色/线索提取，分析整部小说，通过 Bash 调用 add_characters_clues.py 写入 project.json，返回结构化摘要）
- [x] 2.2 提供 `split-narration-segments` agent 的 description，用户通过 `/agents` 命令创建（说书模式片段拆分，按朗读节奏约 4 秒/片段，标记 segment_break，保存 drafts/ 中间文件，返回摘要）
- [x] 2.3 提供 `normalize-drama-script` agent 的 description，用户通过 `/agents` 命令创建（首次生成时调用 normalize_drama_script.py 使用 Gemini 3.1 Pro 生成规范化剧本，后续修改由 agent 直接编辑 Markdown，返回摘要）
- [x] 2.4 提供 `create-episode-script` agent 的 description，用户通过 `/agents` 命令创建（预加载 generate-script skill，调用 generate_script.py 生成 JSON，验证输出，返回摘要）
- [x] 2.5 用户完成 4 个 agent 创建后，审核生成的 agent 文件确保 frontmatter 和 system prompt 符合要求

## 3. 编排 Skill 重写

- [x] 3.1 重写 `manga-workflow/SKILL.md`：状态检测逻辑（读 project.json + 检查 drafts/scripts/characters/storyboards/videos 文件系统）
- [x] 3.2 在 manga-workflow 中定义阶段决策树：缺角色→dispatch analyze-characters-clues、缺 drafts→dispatch split-narration-segments 或 normalize-drama-script（按 content_mode）、缺 scripts→dispatch create-episode-script、缺资产→dispatch 资产生成 subagent
- [x] 3.3 在 manga-workflow 中定义阶段间确认协议：每个 subagent 返回后展示摘要、使用 AskUserQuestion 获取用户确认、支持重做/跳过/继续
- [x] 3.4 在 manga-workflow 中定义上下文传递规则：每个 subagent dispatch 时传递什么参数（项目名、集数、content_mode、文件路径）

## 4. 资产生成 Subagent 适配

- [x] 4.1 定义资产生成 subagent 的 dispatch 方式：确定是创建专用 agent 模板还是使用 general-purpose subagent + 具体任务 prompt
- [x] 4.2 在 manga-workflow 中增加资产生成阶段的 dispatch 逻辑（generate-characters、generate-clues、generate-storyboard、generate-video 各阶段）

## 5. 旧 Agent 清理与文档更新

- [x] 5.1 删除 `agent_runtime_profile/.claude/agents/novel-to-narration-script.md`
- [x] 5.2 删除 `agent_runtime_profile/.claude/agents/novel-to-storyboard-script.md`
- [x] 5.3 更新 `agent_runtime_profile/CLAUDE.md`：替换工作流说明（从两个大 agent 的描述改为新的编排 skill + 聚焦 subagent 架构描述）
- [x] 5.4 更新 `agent_runtime_profile/CLAUDE.md`：添加 skill/agent 边界原则说明
- [x] 5.5 更新 `agent_runtime_profile/.claude/settings.json`：确认新 subagent 的工具权限配置正确

## 6. 主 Agent Prompt 增强

- [x] 6.1 更新 `server/agent_runtime/session_manager.py` 中 `_PERSONA_PROMPT`：增加编排意识——理解工作流阶段、知道何时 dispatch 哪个 subagent
- [x] 6.2 评估 `_build_append_prompt()` 是否需要注入当前工作流阶段状态（可选优化）

## 7. 集成测试与验证

- [x] 7.1 端到端验证：新项目从零开始走完 manga-workflow 全流程（角色提取 → 预处理 → JSON 生成）
- [x] 7.2 验证灵活入口：已有角色的项目直接进入单集预处理阶段
- [x] 7.3 验证增量模式：第二集创建时角色/线索提取的增量追加行为
- [x] 7.4 验证 narration 模式使用 split-narration-segments subagent、drama 模式使用 normalize-drama-script subagent（含 Gemini 脚本调用）
- [x] 7.5 验证 normalize_drama_script.py 的输出格式能被 generate_script.py 正确消费
