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

### Layer 1：Agent 侧 — `PostToolUse` JSON 验证 Hook

**位置**：`server/agent_runtime/session_manager.py`，`_build_options()` 方法

**原理**：SDK `PostToolUse` hook 在每次 `Edit` 或 `Write` 完成后触发。hook 检查目标文件是否为 `.json`；若是，尝试读取并 `json.loads()`；若解析失败，通过 `systemMessage` 向 Agent 注入警告，告知具体错误位置和修复方法，让 Agent **自我发现并立即修复**。

**实现要点**：
- matcher 为 `Write|Edit`（命中两种写文件工具）
- 检查 `file_path` 是否以 `.json` 结尾
- 使用 `pathlib.Path(file_path).read_text()` 读取，然后 `json.loads()`
- 解析失败时返回 `{"systemMessage": "⚠️ 警告：{file_path} 包含无效 JSON，错误：{e}，请立即 Read 该文件，定位问题（如多余逗号 ,,）并 Edit 修复。"}`
- `FileNotFoundError` / `PermissionError` 静默跳过（不干扰正常流程）
- 封装为独立方法 `_build_json_validation_hook()` 返回 async callable
- 追加到现有 `hook_callbacks` 列表末尾（链式 hook，不影响已有文件访问控制 hook）

**效果**：Agent 完成写操作后，若产生了无效 JSON，模型立即收到上下文警告，可在下一轮自动修复，不需要人工介入。

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
| `server/agent_runtime/session_manager.py` | 新增 `_build_json_validation_hook()` 方法；在 `_build_options()` 的 `hook_callbacks` 中追加 | ~25 行 |
| `lib/status_calculator.py` | `_load_episode_script()` 补充捕获 `json.JSONDecodeError` | ~5 行 |

---

## 不在此方案中的内容

- **专用 JSON 编辑脚本（方案 A）**：`settings.json` 已预留 `edit-script-items` 权限，可作为未来增强，不在本次范围内
- **日志格式改进**：Layer 2 修复后，`projects.py` 中的"加载项目元数据失败"理论上不再被 JSON 错误触发，不需要额外改动
- **前端错误处理**：本次聚焦后端，前端已通过 `error` 字段知晓项目加载失败
