# Prompt 约束规则实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在 `prompt_builders_script.py` 中添加约束规则，禁止生成抽象、主观、比喻性描述，确保 image_prompt 和 video_prompt 输出可直接渲染的视觉语言。

**Architecture:** 修改 `build_narration_prompt()` 和 `build_drama_prompt()` 函数中的 prompt 模板，在 image_prompt 和 video_prompt 字段描述中添加 StoryCraft 风格的约束规则。

**Tech Stack:** Python, Prompt Engineering

**Design Doc:** `docs/plans/2026-02-05-prompt-constraints-design.md`

---

### Task 1: 更新 build_narration_prompt 的 image_prompt 约束

**Files:**
- Modify: `lib/prompt_builders_script.py:95-101`

**Step 1: 修改 image_prompt 部分**

将第 95-101 行的 image_prompt 描述替换为：

```python
d. **image_prompt**：生成包含以下字段的对象：
   - scene：用中文描述此刻画面中的具体场景——角色位置、姿态、表情、服装细节，以及可见的环境元素和物品。
     聚焦当下瞬间的可见画面。仅描述摄像机能够捕捉到的具体视觉元素。
     确保描述避免超出此刻画面的元素。排除比喻、隐喻、抽象情绪词、主观评价、多场景切换等无法直接渲染的描述。
     画面应自包含，不暗示过去事件或未来发展。
   - composition：
     - shot_type：镜头类型（Extreme Close-up, Close-up, Medium Close-up, Medium Shot, Medium Long Shot, Long Shot, Extreme Long Shot, Over-the-shoulder, Point-of-view）
     - lighting：用中文描述具体的光源类型、方向和色温（如"左侧窗户透入的暖黄色晨光"）
     - ambiance：用中文描述可见的环境效果（如"薄雾弥漫"、"尘埃飞扬"），避免抽象情绪词
```

**Step 2: 验证修改**

Run: `python -c "from lib.prompt_builders_script import build_narration_prompt; print('OK')"`
Expected: OK

**Step 3: Commit**

```bash
git add lib/prompt_builders_script.py
git commit -m "feat(prompt): add image_prompt constraints to narration mode"
```

---

### Task 2: 更新 build_narration_prompt 的 video_prompt 约束

**Files:**
- Modify: `lib/prompt_builders_script.py:103-108`

**Step 1: 修改 video_prompt 部分**

将第 103-108 行的 video_prompt 描述替换为：

```python
e. **video_prompt**：生成包含以下字段的对象：
   - action：用中文精确描述该时长内主体的具体动作——身体移动、手势变化、表情转换。
     聚焦单一连贯动作，确保在指定时长（4/6/8秒）内可完成。
     排除多场景切换、蒙太奇、快速剪辑等单次生成无法实现的效果。
     排除比喻性动作描述（如"像蝴蝶般飞舞"）。
   - camera_motion：镜头运动（Static, Pan Left, Pan Right, Tilt Up, Tilt Down, Zoom In, Zoom Out, Tracking Shot）
     每个片段仅选择一种镜头运动。
   - ambiance_audio：用中文描述画内音（diegetic sound）——环境声、脚步声、物体声音。
     仅描述场景内真实存在的声音。排除音乐、BGM、旁白、画外音。
   - dialogue：{{speaker, line}} 数组。仅当原文有引号对话时填写。speaker 必须来自 characters_in_segment。
```

**Step 2: 验证修改**

Run: `python -c "from lib.prompt_builders_script import build_narration_prompt; print('OK')"`
Expected: OK

**Step 3: Commit**

```bash
git add lib/prompt_builders_script.py
git commit -m "feat(prompt): add video_prompt constraints to narration mode"
```

---

### Task 3: 更新 build_drama_prompt 的 image_prompt 约束

**Files:**
- Modify: `lib/prompt_builders_script.py:178-184`

**Step 1: 修改 image_prompt 部分**

将第 178-184 行的 image_prompt 描述替换为：

```python
c. **image_prompt**：生成包含以下字段的对象：
   - scene：用中文描述此刻画面中的具体场景——角色位置、姿态、表情、服装细节，以及可见的环境元素和物品。16:9 横屏构图。
     聚焦当下瞬间的可见画面。仅描述摄像机能够捕捉到的具体视觉元素。
     确保描述避免超出此刻画面的元素。排除比喻、隐喻、抽象情绪词、主观评价、多场景切换等无法直接渲染的描述。
     画面应自包含，不暗示过去事件或未来发展。
   - composition：
     - shot_type：镜头类型（Extreme Close-up, Close-up, Medium Close-up, Medium Shot, Medium Long Shot, Long Shot, Extreme Long Shot, Over-the-shoulder, Point-of-view）
     - lighting：用中文描述具体的光源类型、方向和色温（如"左侧窗户透入的暖黄色晨光"）
     - ambiance：用中文描述可见的环境效果（如"薄雾弥漫"、"尘埃飞扬"），避免抽象情绪词
```

**Step 2: 验证修改**

Run: `python -c "from lib.prompt_builders_script import build_drama_prompt; print('OK')"`
Expected: OK

**Step 3: Commit**

```bash
git add lib/prompt_builders_script.py
git commit -m "feat(prompt): add image_prompt constraints to drama mode"
```

---

### Task 4: 更新 build_drama_prompt 的 video_prompt 约束

**Files:**
- Modify: `lib/prompt_builders_script.py:186-191`

**Step 1: 修改 video_prompt 部分**

将第 186-191 行的 video_prompt 描述替换为：

```python
d. **video_prompt**：生成包含以下字段的对象：
   - action：用中文精确描述该时长内主体的具体动作——身体移动、手势变化、表情转换。
     聚焦单一连贯动作，确保在指定时长（4/6/8秒）内可完成。
     排除多场景切换、蒙太奇、快速剪辑等单次生成无法实现的效果。
     排除比喻性动作描述（如"像蝴蝶般飞舞"）。
   - camera_motion：镜头运动（Static, Pan Left, Pan Right, Tilt Up, Tilt Down, Zoom In, Zoom Out, Tracking Shot）
     每个片段仅选择一种镜头运动。
   - ambiance_audio：用中文描述画内音（diegetic sound）——环境声、脚步声、物体声音。
     仅描述场景内真实存在的声音。排除音乐、BGM、旁白、画外音。
   - dialogue：{{speaker, line}} 数组。包含角色对话。speaker 必须来自 characters_in_scene。
```

**Step 2: 验证修改**

Run: `python -c "from lib.prompt_builders_script import build_drama_prompt; print('OK')"`
Expected: OK

**Step 3: Commit**

```bash
git add lib/prompt_builders_script.py
git commit -m "feat(prompt): add video_prompt constraints to drama mode"
```

---

### Task 5: 集成验证

**Files:**
- Test: `projects/test0205/` (现有测试项目)

**Step 1: 验证 prompt 构建函数正常工作**

Run:
```bash
python -c "
from lib.prompt_builders_script import build_narration_prompt, build_drama_prompt

# 测试 narration 模式
prompt = build_narration_prompt(
    project_overview={'synopsis': '测试', 'genre': '古装', 'theme': '复仇', 'world_setting': '古代'},
    style='Photographic',
    style_description='写实风格',
    characters={'角色A': {'description': '测试'}},
    clues={'线索A': {'description': '测试'}},
    segments_md='| E1S01 | 测试 | 4s | 否 | 否 |'
)
assert '排除比喻' in prompt
assert '聚焦当下瞬间' in prompt
print('narration mode: OK')

# 测试 drama 模式
prompt = build_drama_prompt(
    project_overview={'synopsis': '测试', 'genre': '古装', 'theme': '复仇', 'world_setting': '古代'},
    style='Anime',
    style_description='动画风格',
    characters={'角色A': {'description': '测试'}},
    clues={'线索A': {'description': '测试'}},
    scenes_md='| E1S01 | 测试 | 8s | 剧情 | 否 |'
)
assert '排除比喻' in prompt
assert '聚焦单一连贯动作' in prompt
print('drama mode: OK')

print('All constraints verified!')
"
```

Expected: 
```
narration mode: OK
drama mode: OK
All constraints verified!
```

**Step 2: Commit 验证完成**

```bash
git add -A
git commit -m "feat(prompt): complete prompt constraints implementation"
```

---

## 验证清单

完成所有 Task 后，使用以下命令验证：

```bash
# 1. 确认文件已修改
git diff HEAD~4 lib/prompt_builders_script.py | head -100

# 2. 确认关键约束已添加
grep -n "排除比喻" lib/prompt_builders_script.py
grep -n "聚焦当下瞬间" lib/prompt_builders_script.py
grep -n "聚焦单一连贯动作" lib/prompt_builders_script.py

# 3. 确认语法正确
python -c "from lib.prompt_builders_script import build_narration_prompt, build_drama_prompt; print('Import OK')"
```

---

## 后续步骤（可选）

实现完成后，可使用 `/generate-script` 重新生成测试项目的剧本，验证约束效果：

```bash
# 使用 generate-script skill 重新生成
# 然后检查生成的 episode_1.json 中的 image_prompt 和 video_prompt
```
