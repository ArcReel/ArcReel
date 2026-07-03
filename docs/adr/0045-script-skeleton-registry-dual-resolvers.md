---
status: accepted
---

# 剧本骨架注册表以骨架种类为键，轴交互收口进规范/取证双解析器

`SCRIPT_SHAPES` 以 content_mode 单轴为键，只覆盖三种骨架且仅 4 个消费方采用；其余约 19 个文件手写 `== "narration"/"drama"` 字面量分支，数处退化为「非 narration 即 scenes」二值兜底，骨架知识另在 data_validator 四路 if-elif 与 script_editor 判别链各存一份。实际后果已经发生：`project_events` 快照的二值兜底使 ad（`shots`）与 reference_video（`video_units`）两类剧本的条目恒为空，分镜级项目事件对这两类项目从不发出。reference_video 骨架不在表内，意味着每个消费方都需各自特判 video_units，遗漏即静默丢失条目——这是结构性成因，不是个别疏忽。

决定：骨架知识收归零依赖叶子模块 `lib/script_skeleton.py`，全体消费方经它分派。

- **表以骨架种类（skeleton kind）为键**，四值 `segments / scenes / shots / video_units`（域概念见 `CONTEXT.md`「骨架」条）。键本身就是条目数组键，表行退化为 `Skeleton(id_field, chars_field)`；`chars_field` 可为 `None`——video_units 无逐条角色名单（角色以 `references` 中 `type == "character"` 的条目形态存在），表如实声明缺位，消费方拿到 `None` 必须显式决策（自行派生或声明不适用），不提供假字段名使 `get()` 返回空值。
- **content_mode 与 generation_mode 两轴不写入注册表**。轴交互收口进两个解析函数：**规范解析** `(content_mode, generation_mode) → kind`，服务只有项目配置在手的消费方；**取证解析** `script dict → kind`（现 `script_editor.resolve_kind` 迁入），服务手持剧本数据的消费方，保留其数据形状优先的容忍阶梯（partial migration 中间态下编辑能力不可丢失，论证见原 docstring）。输入契约：输入为可能缺字段的 script dict 时走取证解析，规范解析只接受项目级已过校验的模式对。两类需求真实且互斥——status_calculator 论证过计分必须按声明分派、不能嗅探数据形状（残留派生索引不得污染 storyboard 计分），与编辑侧「数据优先」相反，两者不可相互取代。
- **规范解析 fail-loud**：未知/缺失 content_mode 抛 `ValueError`（调用期），`script_shape()` 的 drama 历史兜底删除。依据：`project.json` 层 content_mode 本就是必填且被校验的字段；且全仓对兜底值并无共识（script_shape 与 data_validator 兜底到 drama，status_calculator、text_generation、resolve_kind 最终兜底到 narration），任何静默默认都会掩盖数据损坏，并静默改写另一处代码的既有行为。表自洽（四行齐全、字段合法）import 期 fail-fast，同 `docs/adr/0039` 手法。
- **窄表**：validate 钩子、编辑白名单等行为不写入注册表。写入门槛：骨架的结构事实 + 不引入向上层依赖。data_validator 保留四个 validator 函数，只把自建的轴交互判断换成规范解析；编辑白名单留给 script_editor 双实现收敛的后续工作。
- **测试形态**：集中矩阵（表自洽、规范解析全组合含 ad / reference_video 骨架不变、取证阶梯逐台阶、fail-loud）+ 每个含骨架分派的消费方一条遍历表键的穷尽性参数化断言——第五种骨架出现时，未处置的消费方逐个测试失败，而非复刻 ad 被静默跳过的路径。

## 明确不采用

- **`(content_mode, generation_mode)` 复合键**——把两条独立轴合并进一张表：ad 骨架不随生成路径变（`docs/adr/0033`），复合表里 `(ad, storyboard)` 与 `(ad, reference_video)` 两行内容相同，表结构本身即与事实不符；`CONTEXT.md` 亦明言 reference_video 不是与 grid/storyboard 平级的取值。
- **ASSET_SPECS 式宽表（行为写入注册表）**——ASSET_SPECS 的宽表成立是因为消费方同质（CRUD 路由工厂）；本表消费方异构（校验/导出/计费/事件），四个 validator 签名各异（shots 要 products、scenes 要 language），强行统一是假抽象，且会把 server 层行为反向耦合进 lib 叶子。
- **「缺失默认 narration」的分级兜底**——约半数代码历史上兜底到 drama，任选一边都会静默改变另一处代码的行为；改以显式报错暴露。
- **正则拦截测试（CI 禁二值兜底三元写法）**——评审后不设；`docs/adr/0033` 禁令的强制依靠穷尽性断言与 review。

## Consequences

- 落地实现时，`project_events` 快照 bug 的修复随本收口的实现一并交付（不单独出临时修复 PR），ad 与 reference_video 项目届时恢复分镜级事件推送；video_units 条目快照的 `characters` 从 `references` 过滤 character 类型派生。
- `script_shape()` 兜底删除是行为变更，现有 4 个注册表消费方的传值须逐个核对。
- 约 45 处 content_mode 字面量分派须三分类处置：骨架-结构类（迁移查表/解析器）、内容-行为类（step1 路径、prompt 选择——content_mode 轴的正当业务分派，保留）、轴交互业务规则（如 ad / reference_video 跳过分镜估价，保留）；分类清单、PR 切分与散落分派测试的删留归 PRD 阶段。
- 本 ADR status=accepted：设计已落地为 `lib/script_skeleton.py`（`SKELETONS` 窄表 + 规范解析 `resolve_declared_kind` + 取证解析 `resolve_script_kind`），实现落盘后的新名已写入 `CONTEXT.md`「骨架」条。后续 PR 若想引入复合键、把行为写入注册表、或恢复静默兜底，须先 deprecate 本 ADR。
