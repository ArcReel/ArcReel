# Prompt 约束规则设计

## 背景

在审核 `episode_1.json` 的 `image_prompt` 和 `video_prompt` 时，发现生成的 prompt 包含大量抽象、主观、比喻性描述，导致 AI 图像/视频生成效果不理想。

### 问题示例

| 片段 | 问题描述 | 问题类型 |
|------|---------|---------|
| E1S06 | "画面迅速闪过一本古朴的封面书" | 抽象概念（"穿书"） |
| E1S13 | "像是一座不可撼动的恐怖山峦" | 比喻/隐喻 |
| E1S21 | "多场景混剪" | 多场景切换（技术不可行） |
| E1S23 | "现代灵魂与古代身体的对话" | 抽象心理活动 |
| E1S25 | "鲜红色的染料泼洒"隐喻刑罚 | 隐喻表达 |
| E1S34 | "认知失调" | 抽象情绪词 |

### 参考方案

借鉴 StoryCraft 的 prompt 约束风格：

```
"Scene": "Describe the specific scene being depicted - what is happening in this moment, 
the action or situation being shown, and how it fits into the overall narrative flow. 

Focus on the immediate action and situation. 
Describe the scene : characters (short description only) and objects positions, actions, and interactions. 

Ensure the depiction avoids showing elements beyond this specific moment. 
Exclude any details that suggest a broader story or character arcs. 
The scene should be self-contained, not implying past events or future developments."
```

**核心原则**：
1. 聚焦当下时刻（Focus on the immediate action）
2. 具体可见元素（characters and objects positions, actions, and interactions）
3. 排除抽象扩展（Exclude any details that suggest a broader story）
4. 自包含（self-contained, not implying past events or future developments）

---

## 设计方案

### 修改文件

| 文件 | 修改内容 |
|------|---------|
| `lib/prompt_builders_script.py` | 更新 `build_narration_prompt()` 和 `build_drama_prompt()` 中的 image_prompt 和 video_prompt 约束 |

### 1. image_prompt 约束优化

**当前版本**：
```python
d. **image_prompt**：生成包含以下字段的对象：
   - scene：用中文描述具体场景——角色位置、表情、动作、环境细节。要具体、可视化。一段话。
   - composition：
     - shot_type：镜头类型（Extreme Close-up, Close-up, Medium Close-up, Medium Shot, Medium Long Shot, Long Shot, Extreme Long Shot, Over-the-shoulder, Point-of-view）
     - lighting：用中文描述光源、方向和氛围
     - ambiance：用中文描述整体氛围，与情绪基调匹配
```

**优化版本**：
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

### 2. video_prompt 约束优化

**当前版本**：
```python
e. **video_prompt**：生成包含以下字段的对象：
   - action：用中文精确描述该时长内发生的动作。具体描述运动细节。
   - camera_motion：镜头运动（Static, Pan Left, Pan Right, Tilt Up, Tilt Down, Zoom In, Zoom Out, Tracking Shot）
   - ambiance_audio：用中文描述场景内的声音。禁止出现音乐或 BGM。
   - dialogue：{speaker, line} 数组。仅当原文有引号对话时填写。
```

**优化版本**：
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
   - dialogue：{speaker, line} 数组。仅当原文有引号对话时填写。speaker 必须来自 characters_in_segment。
```

### 3. drama 模式同步更新

`build_drama_prompt()` 函数需要同步应用相同的约束规则：

**image_prompt**（与 narration 模式一致，保留 16:9 横屏说明）：
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

**video_prompt**（与 narration 模式一致，dialogue 字段说明略有不同）：
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
   - dialogue：{speaker, line} 数组。包含角色对话。speaker 必须来自 characters_in_scene。
```

---

## 预期效果

### 修改前（问题示例）

```json
{
  "image_prompt": {
    "scene": "温暖视角：谢渊的身影在大殿中被无限放大，像是一座不可撼动的恐怖山峦。",
    "composition": {
      "ambiance": "压抑而绝望"
    }
  },
  "video_prompt": {
    "action": "镜头快速在几个场景切换：被推开的奏折、假山上晃动的枝叶、偏殿凌乱的床褥。"
  }
}
```

### 修改后（预期效果）

```json
{
  "image_prompt": {
    "scene": "温暖仰视视角，谢渊身着玄色龙袍站在金色龙椅前，双手负于身后，居高临下俯视。大殿内红色立柱排列，地面倒映人影。",
    "composition": {
      "ambiance": "殿内烛火摇曳，光影交错"
    }
  },
  "video_prompt": {
    "action": "谢渊缓缓抬起右手，衣袖随动作轻摆，指尖指向画面前方。"
  }
}
```

---

## 实现步骤

1. 修改 `lib/prompt_builders_script.py` 中的 `build_narration_prompt()` 函数
2. 修改 `lib/prompt_builders_script.py` 中的 `build_drama_prompt()` 函数
3. 使用现有测试项目重新生成剧本，验证效果

---

## 验证方法

1. 使用 `/generate-script` 重新生成测试项目的剧本
2. 检查生成的 `image_prompt` 和 `video_prompt` 是否符合约束
3. 抽样进行图像/视频生成测试，评估效果
