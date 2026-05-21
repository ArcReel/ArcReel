# 角色参考图功能实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 添加角色参考图上传功能，生成角色设计图时自动使用参考图作为 AI 输入

**Architecture:** 
- 后端：新增 `character_ref` 上传类型，修改生成端点读取参考图
- 前端：角色弹窗新增参考图上传区域（上下排布），保存时一并上传
- CLI：移除 `--ref` 参数，自动从 project.json 读取

**Tech Stack:** Python/FastAPI, JavaScript, HTML/Tailwind CSS

---

## Task 1: 后端 - 添加 character_ref 上传类型

**Files:**
- Modify: `webui/server/routers/files.py:15-20` (ALLOWED_EXTENSIONS)
- Modify: `webui/server/routers/files.py:60-100` (upload_file 函数)

**Step 1: 在 ALLOWED_EXTENSIONS 添加 character_ref 类型**

在 `files.py` 的 `ALLOWED_EXTENSIONS` 字典中添加新类型：

```python
ALLOWED_EXTENSIONS = {
    "source": [".txt", ".md", ".doc", ".docx"],
    "character": [".png", ".jpg", ".jpeg", ".webp"],
    "character_ref": [".png", ".jpg", ".jpeg", ".webp"],  # 新增
    "clue": [".png", ".jpg", ".jpeg", ".webp"],
    "storyboard": [".png", ".jpg", ".jpeg", ".webp"],
}
```

**Step 2: 在 upload_file 函数中添加 character_ref 处理逻辑**

在 `upload_file` 函数的 `if upload_type == "character":` 分支后添加：

```python
elif upload_type == "character_ref":
    target_dir = project_dir / "characters" / "refs"
    if name:
        filename = f"{name}.png"
    else:
        filename = f"{Path(file.filename).stem}.png"
```

**Step 3: 添加自动更新 reference_image 字段的逻辑**

在文件保存后的元数据更新部分（`if upload_type == "character" and name:` 后面）添加：

```python
if upload_type == "character_ref" and name:
    try:
        pm.update_character_reference_image(project_name, name, f"characters/refs/{filename}")
    except KeyError:
        pass  # 角色不存在，忽略
```

**Step 4: 运行服务器验证语法正确**

Run: `cd /Users/pollochen/Documents/ai-anime/.worktrees/character-reference-image && python -c "from webui.server.routers.files import router; print('OK')"`
Expected: `OK`

**Step 5: Commit**

```bash
git add webui/server/routers/files.py
git commit -m "feat(files): add character_ref upload type"
```

---

## Task 2: 后端 - ProjectManager 添加 reference_image 更新方法

**Files:**
- Modify: `lib/project_manager.py`

**Step 1: 添加 update_character_reference_image 方法**

在 `ProjectManager` 类中添加方法（参考现有的 `update_project_character_sheet`）：

```python
def update_character_reference_image(self, project_name: str, char_name: str, ref_path: str) -> dict:
    """
    更新角色的参考图路径
    
    Args:
        project_name: 项目名称
        char_name: 角色名称
        ref_path: 参考图相对路径
        
    Returns:
        更新后的项目数据
    """
    project = self.load_project(project_name)
    
    if "characters" not in project or char_name not in project["characters"]:
        raise KeyError(f"角色 '{char_name}' 不存在")
    
    project["characters"][char_name]["reference_image"] = ref_path
    self.save_project(project_name, project)
    return project
```

**Step 2: 验证导入正常**

Run: `cd /Users/pollochen/Documents/ai-anime/.worktrees/character-reference-image && python -c "from lib.project_manager import ProjectManager; pm = ProjectManager(); print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add lib/project_manager.py
git commit -m "feat(project_manager): add update_character_reference_image method"
```

---

## Task 3: 后端 - characters.py 添加 reference_image 字段支持

**Files:**
- Modify: `webui/server/routers/characters.py:20-25` (UpdateCharacterRequest)
- Modify: `webui/server/routers/characters.py:55-65` (update_character 函数)

**Step 1: 在 UpdateCharacterRequest 添加 reference_image 字段**

```python
class UpdateCharacterRequest(BaseModel):
    description: Optional[str] = None
    voice_style: Optional[str] = None
    character_sheet: Optional[str] = None
    reference_image: Optional[str] = None  # 新增
```

**Step 2: 在 update_character 函数中处理 reference_image**

在 `if req.character_sheet is not None:` 后添加：

```python
if req.reference_image is not None:
    char["reference_image"] = req.reference_image
```

**Step 3: 验证语法正确**

Run: `cd /Users/pollochen/Documents/ai-anime/.worktrees/character-reference-image && python -c "from webui.server.routers.characters import router; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add webui/server/routers/characters.py
git commit -m "feat(characters): add reference_image field to update API"
```

---

## Task 4: 后端 - generate.py 使用参考图生成角色设计图

**Files:**
- Modify: `webui/server/routers/generate.py:280-330` (generate_character 函数)

**Step 1: 修改 generate_character 函数读取参考图**

在 `generate_character` 函数中，检查角色是否存在后，添加读取参考图的逻辑：

```python
@router.post("/projects/{project_name}/generate/character/{char_name}")
async def generate_character(
    project_name: str,
    char_name: str,
    req: GenerateCharacterRequest
):
    """
    生成角色设计图（首次生成或重新生成）

    使用 MediaGenerator 自动处理版本管理。
    若角色有 reference_image，自动作为参考图传入。
    """
    try:
        project = pm.load_project(project_name)
        project_path = pm.get_project_path(project_name)
        generator = get_media_generator(project_name)

        # 检查角色是否存在
        if char_name not in project.get("characters", {}):
            raise HTTPException(status_code=404, detail=f"角色 '{char_name}' 不存在")

        char_data = project["characters"][char_name]

        # 获取画面比例（角色设计图 3:4）
        aspect_ratio = get_aspect_ratio(project, "characters")

        # 使用共享库构建 Prompt（确保与 Skill 侧一致）
        style = project.get("style", "")
        full_prompt = build_character_prompt(char_name, req.prompt, style)

        # 读取参考图（如果存在）
        reference_images = None
        ref_path = char_data.get("reference_image")
        if ref_path:
            ref_full_path = project_path / ref_path
            if ref_full_path.exists():
                reference_images = [ref_full_path]

        # 使用 MediaGenerator 生成图片（自动处理版本管理）
        _, new_version = await generator.generate_image_async(
            prompt=full_prompt,
            resource_type="characters",
            resource_id=char_name,
            reference_images=reference_images,  # 传入参考图
            aspect_ratio=aspect_ratio,
            image_size="2K"
        )

        # 更新 project.json 中的 character_sheet
        project["characters"][char_name]["character_sheet"] = f"characters/{char_name}.png"
        pm.save_project(project_name, project)

        return {
            "success": True,
            "version": new_version,
            "file_path": f"characters/{char_name}.png",
            "created_at": generator.versions.get_versions("characters", char_name)["versions"][-1]["created_at"]
        }

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

**Step 2: 验证语法正确**

Run: `cd /Users/pollochen/Documents/ai-anime/.worktrees/character-reference-image && python -c "from webui.server.routers.generate import router; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add webui/server/routers/generate.py
git commit -m "feat(generate): use reference_image when generating character sheet"
```

---

## Task 5: CLI - 移除 --ref 参数，自动从 project.json 读取

**Files:**
- Modify: `.claude/skills/generate-characters/scripts/generate_character.py`

**Step 1: 修改 generate_character 函数自动读取参考图**

```python
def generate_character(
    project_name: str,
    character_name: str,
) -> Path:
    """
    生成角色设计图

    Args:
        project_name: 项目名称
        character_name: 角色名称

    Returns:
        生成的图片路径
    """
    pm = ProjectManager()
    project_dir = pm.get_project_path(project_name)

    # 从 project.json 获取角色信息
    project = pm.load_project(project_name)

    description = ""
    style = project.get('style', '')
    reference_images = None

    if 'characters' in project and character_name in project['characters']:
        char_info = project['characters'][character_name]
        description = char_info.get('description', '')
        
        # 自动读取参考图
        ref_path = char_info.get('reference_image')
        if ref_path:
            ref_full_path = project_dir / ref_path
            if ref_full_path.exists():
                reference_images = [ref_full_path]
                print(f"📎 使用参考图: {ref_full_path}")

    if not description:
        raise ValueError(f"角色 '{character_name}' 的描述为空，请先在 project.json 中添加描述")

    # 构建 prompt
    prompt = build_character_prompt(character_name, description, style)

    # 生成图片（带自动版本管理）
    generator = MediaGenerator(project_dir)

    print(f"🎨 正在生成角色设计图: {character_name}")
    print(f"   描述: {description[:50]}...")

    output_path, version = generator.generate_image(
        prompt=prompt,
        resource_type="characters",
        resource_id=character_name,
        reference_images=reference_images,
        aspect_ratio="3:4"
    )

    print(f"✅ 角色设计图已保存: {output_path} (版本 v{version})")

    # 更新 project.json 中的 character_sheet 路径
    relative_path = f"characters/{character_name}.png"
    pm.update_project_character_sheet(project_name, character_name, relative_path)
    print("✅ project.json 已更新")

    return output_path
```

**Step 2: 简化 main 函数，移除 --ref 参数**

```python
def main():
    parser = argparse.ArgumentParser(description='生成角色设计图')
    parser.add_argument('project', help='项目名称')
    parser.add_argument('character', help='角色名称')
    # 移除 --ref 参数

    args = parser.parse_args()

    try:
        output_path = generate_character(
            args.project,
            args.character,
        )
        print(f"\n🖼️  请查看生成的图片: {output_path}")

    except Exception as e:
        print(f"❌ 错误: {e}")
        sys.exit(1)
```

**Step 3: 验证语法正确**

Run: `cd /Users/pollochen/Documents/ai-anime/.worktrees/character-reference-image && python -c "from pathlib import Path; exec(open('.claude/skills/generate-characters/scripts/generate_character.py').read().split('if __name__')[0]); print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add .claude/skills/generate-characters/scripts/generate_character.py
git commit -m "feat(cli): auto-read reference_image from project.json, remove --ref arg"
```

---

## Task 6: 前端 - HTML 添加参考图上传区域

**Files:**
- Modify: `webui/project.html:370-400` (character-modal 内的表单)

**Step 1: 在"声音风格"字段后、"角色设计图"字段前添加参考图上传区域**

在 `char-voice` 输入框的 `</div>` 后，`角色设计图` label 前添加：

```html
<div>
    <label class="block text-sm font-medium text-gray-300 mb-1">参考图（可选）</label>
    <div id="char-ref-drop" class="drop-zone rounded-lg p-4 text-center cursor-pointer relative">
        <div id="char-ref-preview" class="hidden mb-2">
            <img src="" alt="参考图预览" class="max-h-32 mx-auto rounded">
        </div>
        <div id="char-ref-placeholder">
            <svg class="mx-auto h-8 w-8 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
            </svg>
            <p class="mt-1 text-xs text-gray-400">点击或拖拽上传参考图（用于生成设计图）</p>
        </div>
        <input type="file" id="char-ref-input" accept="image/*" class="hidden">
    </div>
</div>
```

**Step 2: 验证 HTML 文件语法正确（无明显错误）**

Run: `cd /Users/pollochen/Documents/ai-anime/.worktrees/character-reference-image && grep -c "char-ref-input" webui/project.html`
Expected: `1`

**Step 3: Commit**

```bash
git add webui/project.html
git commit -m "feat(ui): add reference image upload area in character modal"
```

---

## Task 7: 前端 - JavaScript 处理参考图上传逻辑

**Files:**
- Modify: `webui/js/project/characters.js`

**Step 1: 在 openCharacterModal 函数中初始化参考图预览**

在 `form.reset();` 后添加参考图相关的重置和显示逻辑：

```javascript
// 重置参考图区域
document.getElementById("char-ref-preview").classList.add("hidden");
document.getElementById("char-ref-placeholder").classList.remove("hidden");
document.getElementById("char-ref-input").value = "";

// ... 现有代码 ...

// 在编辑模式下显示已有参考图
if (charName && state.currentProject.characters[charName]) {
    const char = state.currentProject.characters[charName];
    // ... 现有代码 ...
    
    // 显示参考图（如果有）
    if (char.reference_image) {
        const refPreview = document.getElementById("char-ref-preview");
        refPreview.querySelector("img").src = `${API.getFileUrl(state.projectName, char.reference_image)}?t=${state.cacheBuster}`;
        refPreview.classList.remove("hidden");
        document.getElementById("char-ref-placeholder").classList.add("hidden");
    }
}
```

**Step 2: 添加参考图上传区域的事件监听**

在文件末尾或合适位置添加初始化函数：

```javascript
// 初始化参考图上传区域
export function initCharacterRefUpload() {
    const dropZone = document.getElementById("char-ref-drop");
    const input = document.getElementById("char-ref-input");
    const preview = document.getElementById("char-ref-preview");
    const placeholder = document.getElementById("char-ref-placeholder");

    if (!dropZone || !input) return;

    // 点击上传
    dropZone.addEventListener("click", () => input.click());

    // 文件选择
    input.addEventListener("change", (e) => {
        const file = e.target.files[0];
        if (file) {
            showRefPreview(file);
        }
    });

    // 拖拽上传
    dropZone.addEventListener("dragover", (e) => {
        e.preventDefault();
        dropZone.classList.add("border-blue-500");
    });

    dropZone.addEventListener("dragleave", () => {
        dropZone.classList.remove("border-blue-500");
    });

    dropZone.addEventListener("drop", (e) => {
        e.preventDefault();
        dropZone.classList.remove("border-blue-500");
        const file = e.dataTransfer.files[0];
        if (file && file.type.startsWith("image/")) {
            // 设置到 input 以便后续读取
            const dt = new DataTransfer();
            dt.items.add(file);
            input.files = dt.files;
            showRefPreview(file);
        }
    });

    function showRefPreview(file) {
        const reader = new FileReader();
        reader.onload = (e) => {
            preview.querySelector("img").src = e.target.result;
            preview.classList.remove("hidden");
            placeholder.classList.add("hidden");
        };
        reader.readAsDataURL(file);
    }
}
```

**Step 3: 修改 saveCharacter 函数处理参考图上传**

在 saveCharacter 函数中，处理参考图上传：

```javascript
export async function saveCharacter() {
    const mode = document.getElementById("char-edit-mode").value;
    const originalName = document.getElementById("char-original-name").value;
    const name = document.getElementById("char-name").value.trim();
    const description = document.getElementById("char-description").value.trim();
    const voiceStyle = document.getElementById("char-voice").value.trim();
    const imageInput = document.getElementById("char-image-input");
    const refInput = document.getElementById("char-ref-input");  // 新增

    if (!name || !description) {
        alert("请填写必填字段");
        return;
    }

    try {
        // 如果有新参考图，先上传
        let referenceImage = null;
        if (refInput.files.length > 0) {
            const result = await API.uploadFile(state.projectName, "character_ref", refInput.files[0], name);
            referenceImage = result.path;
        }

        // 如果有新设计图，上传
        let characterSheet = null;
        if (imageInput.files.length > 0) {
            const result = await API.uploadFile(state.projectName, "character", imageInput.files[0], name);
            characterSheet = result.path;
        }

        if (mode === "add") {
            await API.addCharacter(state.projectName, name, description, voiceStyle);
            if (referenceImage) {
                await API.updateCharacter(state.projectName, name, { reference_image: referenceImage });
            }
            if (characterSheet) {
                await API.updateCharacter(state.projectName, name, { character_sheet: characterSheet });
            }
        } else {
            // 编辑模式
            if (originalName !== name) {
                // 名称变更，需要先删除旧的再添加新的
                await API.deleteCharacter(state.projectName, originalName);
                await API.addCharacter(state.projectName, name, description, voiceStyle);
            } else {
                await API.updateCharacter(state.projectName, name, { description, voice_style: voiceStyle });
            }
            if (referenceImage) {
                await API.updateCharacter(state.projectName, name, { reference_image: referenceImage });
            }
            if (characterSheet) {
                await API.updateCharacter(state.projectName, name, { character_sheet: characterSheet });
            }
        }

        closeAllModals();
        await loadProject();
    } catch (error) {
        alert("保存失败: " + error.message);
    }
}
```

**Step 4: 验证 JavaScript 语法正确**

Run: `cd /Users/pollochen/Documents/ai-anime/.worktrees/character-reference-image && node --check webui/js/project/characters.js 2>&1 || echo "Syntax check done"`
Expected: 无错误输出或 "Syntax check done"

**Step 5: Commit**

```bash
git add webui/js/project/characters.js
git commit -m "feat(ui): implement reference image upload and preview in character modal"
```

---

## Task 8: 前端 - 初始化参考图事件监听

**Files:**
- Modify: `webui/js/project/events.js` (或项目的主初始化文件)

**Step 1: 在适当位置调用 initCharacterRefUpload**

在 events.js 的初始化函数中添加调用：

```javascript
import { initCharacterRefUpload } from "./characters.js";

// 在 DOMContentLoaded 或初始化函数中
initCharacterRefUpload();
```

**Step 2: 确认导入和调用正确**

Run: `cd /Users/pollochen/Documents/ai-anime/.worktrees/character-reference-image && grep -c "initCharacterRefUpload" webui/js/project/events.js`
Expected: `1` 或 `2`

**Step 3: Commit**

```bash
git add webui/js/project/events.js
git commit -m "feat(ui): initialize character reference image upload on page load"
```

---

## Task 9: 集成测试 - 手动验证完整流程

**Step 1: 启动 WebUI 服务器**

Run: `cd /Users/pollochen/Documents/ai-anime/.worktrees/character-reference-image && python -m webui.server.main &`

**Step 2: 手动测试流程**

1. 打开浏览器访问 http://localhost:8000
2. 选择或创建一个测试项目
3. 添加新角色，上传参考图
4. 保存角色
5. 点击"生成设计图"
6. 验证生成的设计图是否参考了上传的图片

**Step 3: 验证 project.json 结构**

检查项目的 `project.json` 是否正确包含 `reference_image` 字段：

```bash
cat projects/test-project/project.json | python -m json.tool | grep -A5 "characters"
```

Expected: 包含 `"reference_image": "characters/refs/xxx.png"`

**Step 4: 停止测试服务器**

Run: `pkill -f "python -m webui.server.main"`

---

## Task 10: 最终提交和清理

**Step 1: 更新设计文档状态**

将 `docs/plans/2026-02-05-character-reference-image-design.md` 中的状态改为"已实现"。

**Step 2: 最终 Commit**

```bash
git add docs/plans/2026-02-05-character-reference-image-design.md
git commit -m "docs: mark character reference image feature as implemented"
```

**Step 3: 查看所有提交**

```bash
git log --oneline -10
```

---

## 实现清单

| Task | 描述 | 预计时间 |
|------|------|---------|
| 1 | 后端 files.py 添加 character_ref 上传类型 | 3 min |
| 2 | 后端 ProjectManager 添加更新方法 | 3 min |
| 3 | 后端 characters.py 添加 reference_image 字段 | 2 min |
| 4 | 后端 generate.py 使用参考图 | 5 min |
| 5 | CLI 移除 --ref 参数，自动读取 | 5 min |
| 6 | 前端 HTML 添加参考图上传区域 | 3 min |
| 7 | 前端 JS 处理上传逻辑 | 10 min |
| 8 | 前端 JS 初始化事件监听 | 2 min |
| 9 | 集成测试 | 5 min |
| 10 | 最终提交和清理 | 2 min |

**总计：约 40 分钟**
