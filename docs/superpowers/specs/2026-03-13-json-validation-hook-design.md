# JSON 文件写入验证：防御 Agent 损坏文件设计文档

**日期**：2026-03-13
**状态**：已批准
**分支**：`fix/json-validation-hook`

---

## 问题背景

Agent（Claude Agent SDK 会话）在调用 `Edit` 工具修改剧本 JSON 文件时，生成的 `new_string` 末尾多出了逗号，与文件中原有逗号合并，产生 `},,` 双逗号，导致文件成为无效 JSON。

### 完整级联失败链

```
Agent Edit episode_2.json
  → new_string 末尾多余逗号 → },, （无效 JSON）
  → project_events.py: 优雅跳过（WARNING + continue）✓ 无影响
  → routers/projects.py list_projects():
      → calculator.calculate_project_status(name, project)
          → _load_episode_script()
              → pm.load_script() → json.JSONDecodeError
              → 只 catch FileNotFoundError，JSON 错误上抛！
      → 宽泛 except Exception 捕获 → "加载项目元数据失败"
  → 项目大厅整个项目显示为损坏/不可用 ✗
```

### 受影响代码

- `server/agent_runtime/session_manager.py` — Agent 的 `Edit`/`Write` 工具无任何 JSON 验证
- `lib/status_calculator.py` — `_load_episode_script()` 只捕获 `FileNotFoundError`，`json.JSONDecodeError` 上抛导致级联崩溃

---

## 解决方案

两层防御，互相独立：

### Layer 1：Agent 侧 — `PreToolUse` JSON 验证 Hook

**位置**：`server/agent_runtime/session_manager.py`，`_build_options()` 方法

**原理**：SDK `PreToolUse` hook 在每次 `Edit` 或 `Write` 执行**之前**触发。hook 检查目标文件是否为 `.json`；若是，**模拟**本次写入的结果（Write 直接取 `content`；Edit 读取当前文件并模拟 `old_string→new_string` 替换），然后 `json.loads()` 校验。若结果会成为无效 JSON，返回 `permissionDecision: "deny"` 拦截操作，让 Agent **在操作落盘前修正输入并重试**。

**实现要点**：
- matcher 为 `Write|Edit`（命中两种写文件工具）
- 检查 `file_path` 是否以 `.json` 结尾
- Write：校验 `content` 参数；Edit：读取当前文件并模拟替换（尊重 `replace_all`），`old_string` 未匹配则跳过（Edit 会自行失败）
- 额外拦截 `new_string` 中的弯引号（U+201C/U+201D 等），避免 Claude Code 内部归一化后弯引号漏入文件破坏 JSON
- 校验失败时返回 `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "..."}}`
- 读取失败（`OSError`）等情形静默跳过（不干扰正常流程）
- 同时把改前内容备份到 `json_backups`，供配套的 PostToolUse hook 在结果仍损坏时还原文件
- 封装为独立方法 `_build_json_validation_hook()` 返回 async callable

**效果**：Agent 写 JSON 前若会产生无效结果，操作被直接拦截并附带修复提示，Agent 在下一轮修正输入重试，损坏内容根本不会落盘。

### Layer 2：服务读取侧 — `_load_episode_script` 防御性修复

**位置**：`lib/status_calculator.py`，`_load_episode_script()` 方法

**原理**：补充捕获 `(json.JSONDecodeError, ValueError)`，记录 WARNING 日志，返回 `('generated', None)` 表示文件存在但不可读，状态计算降级而不崩溃。

**实现要点**：
```python
except (json.JSONDecodeError, ValueError) as e:
    logger.warning(
        "剧本 JSON 损坏，跳过状态计算 project=%s file=%s: %s",
        project_name, script_file, e
    )
    return 'generated', None
```

- 返回 `'generated'` 而非 `'none'`：文件存在说明剧本已生成过，只是当前损坏
- 下游调用者对 `script=None` 的处理需确认兼容（已确认：`enrich_project` 和 `calculate_project_status` 的调用链对 `None` 安全）

**效果**：单个 episode JSON 文件损坏，不再导致整个项目在大厅崩溃，影响范围收缩到该集的状态计算字段。

---

## 修改文件汇总

| 文件 | 修改内容 | 行数估计 |
|------|---------|--------|
| `server/agent_runtime/session_manager.py` | 新增 `_build_json_validation_hook()`（PreToolUse 拦截 + 备份）方法；在 `_build_options()` 的 `hook_callbacks` 中注册 PreToolUse/PostToolUse | — |
| `lib/status_calculator.py` | `_load_episode_script()` 补充捕获 `json.JSONDecodeError` | ~5 行 |

---

## 不在此方案中的内容

- **专用 JSON 编辑脚本（方案 A）**：通过 `edit-script-items` skill 脚本做结构化编辑，可作为未来增强，不在本次范围内
- **日志格式改进**：Layer 2 修复后，`projects.py` 中的"加载项目元数据失败"理论上不再被 JSON 错误触发，不需要额外改动
- **前端错误处理**：本次聚焦后端，前端已通过 `error` 字段知晓项目加载失败
