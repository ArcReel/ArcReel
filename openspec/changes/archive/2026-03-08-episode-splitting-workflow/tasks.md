## 1. 核心脚本开发

- [x] 1.1 创建共享工具函数模块 `_text_utils.py`：实现 `count_chars(text)` 函数（含标点不含空行的字数计数）和 `find_char_offset(text, target_count)` 函数（有效字数 → 原文字符偏移的转换），供两个脚本共享
- [x] 1.2 创建 `peek_split_point.py` 脚本：接收 `--source`、`--target`（目标字数）、`--context`（上下文字数，默认 200）参数；使用 `_text_utils` 定位切分点；输出切分点前后上下文文本 + 元信息（总字数、目标位置字符偏移、推荐的附近自然断点列表）
- [x] 1.3 创建 `split_episode.py` 脚本：接收 `--source`、`--episode`、`--anchor`（切分点前的文本片段，约 10-20 字符）参数；在原文中查找 anchor 文本，在其末尾处切分；支持 `--dry-run` 模式（仅展示切分预览：前文末尾 + 后文开头各 50 字，不写文件）；实际执行时生成 `source/episode_{N}.txt`（前半部分）和 `source/_remaining.txt`（后半部分）；anchor 匹配到多处时报错要求更长锚点；原文件不动

## 2. 权限与配置

- [x] 2.1 在 `settings.json` 的 `permissions.allow` 中添加 `peek_split_point.py` 和 `split_episode.py` 的 Bash 执行权限

## 3. 工作流集成

- [x] 3.1 更新 `manga-workflow/SKILL.md` 阶段 2：增加前置检查逻辑——检查 `source/episode_{N}.txt` 是否存在，不存在时触发分集规划流程（询问目标字数 → 调用 peek → agent 建议断点 → 用户确认 → 调用 split）
- [x] 3.2 更新 `normalize-drama-script.md` subagent：dispatch 时明确使用 `--source source/episode_{N}.txt` 参数
- [x] 3.3 更新 `split-narration-segments.md` subagent：dispatch 时明确指定读取 `source/episode_{N}.txt`

## 4. 验证

- [x] 4.1 验证 peek 脚本对中文小说的字数计数准确性（含标点、不含空行）
- [x] 4.2 验证 split 脚本的切分结果：episode 文件 + remaining 文件的内容完整拼接 = 原文
- [x] 4.3 端到端验证：上传完整小说 → 分集切分 → normalize_drama_script.py --source episode_1.txt → generate_script.py 生成 JSON
