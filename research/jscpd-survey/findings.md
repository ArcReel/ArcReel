# 用 jscpd 摸底 tests/ 近似重复的规模，量出 #2266 判据的召回缺口

调查票 [#2267](https://github.com/ArcReel/ArcReel/issues/2267)，地图 [#2250](https://github.com/ArcReel/ArcReel/issues/2250)。
基线 commit `956758f4f`。**未做任何删除或合并，未改动 `lib/` `server/` `tests/`，未往任何 `package.json` 写依赖。**

## 环境与可复跑命令

| 项 | 值 |
| --- | --- |
| jscpd | **5.1.1**（`pnpm dlx jscpd --version` 自报 `cpd 5.1.1`，Rust 实现） |
| Node / pnpm | v24.12.0 / 10.33.2 |
| Python | 3.12.10（系统解释器；本票**未** `uv sync`，脚本全是 stdlib `ast` + `json`） |
| 扫描目标 | 后端 `tests/`，515 个 `.py`、186,999 行、1,098,678 token |
| 检测模式 | jscpd 默认 mode（未传 `-m`），`--format python` |

```bash
# 分层扫描（本票实跑七档）
sh research/jscpd-survey/run_jscpd.sh 10 25 50 75 100 150 200

# clone 片段 -> 测试函数 的映射与「整体近似重复族」
python3 research/jscpd-survey/map_clones.py research/jscpd-survey/raw/k25/jscpd-report.json \
        --cover 0.8 --json research/jscpd-survey/raw/mapped-k25.json
python3 research/jscpd-survey/dump_families.py research/jscpd-survey/raw/mapped-k25.json   # 逐族源码，供人工判

# #2266 判据（原样取自 research/redundant-tests 分支）与召回核对
python3 research/jscpd-survey/dup_body.py tests
python3 research/jscpd-survey/check_pairs.py 10 25 50 75 100 150 200

# 辅助分析
python3 research/jscpd-survey/stats_table.py 10 25 50 75 100 150 200
python3 research/jscpd-survey/top_files.py 50 15
python3 research/jscpd-survey/sample_partial.py 50 15 2267
python3 research/jscpd-survey/cross_file_helpers.py 50
python3 research/jscpd-survey/helper_groups.py research/jscpd-survey/raw/cross-file-helpers-k50.txt
```

成本：一次全量扫描 **1.59 s 墙钟**（检测本身约 110 ms，其余是 `pnpm dlx` 解析已缓存的包）；`dup_body.py` 1.13 s。
两者都比 #2265 的 cov-context 采集（269 s）便宜两个量级，**采集成本不构成任何障碍**。

---

## 1. 规模：token 级近似重复有多大体量

| min-tokens | 分析文件 | clone 对 | 重复行 | 重复行占比 | 重复 token 占比 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 515 | 5724 | 42918 | 22.95% | 19.78% |
| 25 | 515 | 3809 | 30562 | 16.34% | 15.89% |
| **50** | 514 | **1109** | **12108** | **6.48%** | **7.25%** |
| 75 | 513 | 359 | 5219 | 2.79% | 3.20% |
| 100 | 509 | 120 | 2166 | 1.16% | 1.36% |
| 150 | 495 | 19 | 503 | 0.27% | 0.30% |
| 200 | 483 | 2 | 53 | 0.03% | 0.04% |

曲线在 25 到 50 之间断崖（3809 -> 1109 对），50 到 100 再掉一个量级。**k=50 是唯一同时兼顾信噪比与体量的档位**：
6.48% 重复行、1109 对、涉及 **233 个文件**（占 501 个测试文件的 46.5%）。

k=50 时被 clone 覆盖行数最多的文件（去重计行）：

| 覆盖行 | clone 对 | 文件 |
| ---: | ---: | --- |
| 1008 | 115 | `tests/integration/server/services/test_execute_reference_video_task.py` |
| 642 | 74 | `tests/integration/server/services/test_execute_video_task.py` |
| 609 | 49 | `tests/integration/server/media_tools/test_videos.py` |
| 560 | 76 | `tests/unit/lib/custom_provider/test_declarative_video_backend.py` |
| 532 | 58 | `tests/integration/lib/test_workflow_state.py` |
| 531 | 56 | `tests/integration/server/services/test_cost_estimation_service.py` |
| 432 | 62 | `tests/unit/server/routers/test_versions_router.py` |

最大的单个 clone 块是 `test_execute_reference_video_task.py` 内部的 **201 token / 31 行**（同文件，L408-438 与 L505-535）。
全仓库 **没有任何 200 token 以上的跨文件 clone**，k=200 只剩 2 对且都在这一个文件里。

原始数据：`raw/k*/jscpd-report.json`、`raw/stats-table.md`、`raw/run-log-top-files-k50.txt`。

### 关键结构事实：重复几乎全在「用例内部」，不在「用例之间」

把每个 clone 片段映射回它所在的测试函数（`map_clones.py`），k=50 的 1109 对分解为：

| 类别 | 对数 | 占比 |
| --- | ---: | ---: |
| 同文件 | 958 | 86.4% |
| 两端宿主都是 test 函数 | 969 | 87.4% |
| 片段落在 fixture / 辅助函数 / 类体 / 模块级 | 140 | 12.6% |
| **两端各自覆盖宿主函数体 80% 以上（「整体近似重复」）** | **32** | **2.9%** |

也就是说：**jscpd 报出的重复，97.1% 是用例内部的 setup / 打桩样板，只有 2.9% 是「两条用例整体长得几乎一样」。**
随机抽 15 条非整体重复对逐条看（`raw/sample-partial-k50.txt`，seed 2267），**15/15 全是构造样板或重复的局部替身**，
零条属于「两条用例本身重复」。

---

## 2. 成色：正当重复 vs 真正可删

判定对象取**信号最浓的那一层**：同文件、两端覆盖宿主 80% 以上、宿主互不相同的近似重复族。
取 k=25（k=50 的超集，63 族包含 k=50 的 25 族），**全量枚举 63 族、136 个测试函数，逐族看源码判定**，
不抽样（判定明细见 `raw/verdicts-k25.md`）。

| 判定 | 定义 | 族数 | 占比 |
| --- | --- | ---: | ---: |
| **A 可删** | 输入与断言都已被另一条实质覆盖（含 CONTRIBUTING「重复弱化」） | **3** | **4.8%** |
| B 可合并 | 同形、只差输入 / 期望值，或断言互补而 setup 重复；可并成一条 `@parametrize` 或一条用例；**减重，不可删** | 27 | 42.9% |
| C 正当重复—各守不同契约点 | 断言形状不同，或输入差异本身就是被守的契约（#2265 的 pricing 形态） | 28 | 44.4% |
| D 正当重复—setup 样板 | 重复段是构造样板，用例本身不重复（cover 阈值在短用例上的假阳） | 5 | 7.9% |

**可处置率（按 CONTRIBUTING 三步处置口径，只有 A 计入分子）= 3/63 = 4.8%。**

对照基准：

| 路线 | 候选 | 可处置 | 可处置率 |
| --- | ---: | ---: | ---: |
| mutmut A 组（#2258） | 54 | 3 | 5.6% |
| mutmut B 组（#2258） | 76 | 45 | 59.2% |
| cov-context L4（#2265） | 130 判 | 0 | **0%** |
| **jscpd 整体近似重复层（本票）** | **63 族全判** | **3** | **4.8%** |

结论：**jscpd 这一层与 mutmut A 组同量级，明确优于 cov-context 的 0%，但远不及 mutmut B 组。**
若把 B 类「可合并」也算成一个处置动作（它不属 CONTRIBUTING 三步处置，是重构），
「有动作可做」的比例是 30/63 = **47.6%**，这是本票产出里体量最大的那一块，见第 4 节。

三条 A 逐条：

1. `tests/unit/server/routers/test_providers_api.py::TestPatchProviderConfig::test_returns_204`
   与同类 `::test_non_null_value_calls_set` 同路由、同请求形状（`{"api_key": ...}`）、同 20 行建 app 样板，
   断言 `assert resp.status_code == 204` 是后者断言集的**真子集**。典型「重复弱化」。
2. `tests/integration/lib/config/test_config_resolver.py::TestVideoGenerateAudio::test_project_none_skips_override`
   等价于同类 `::test_global_true`，**即 #2266 存量第 1 组**。
3. `tests/unit/lib/test_logging_persistence.py::test_file_handler_disabled_by_env`
   与 `test_disabled_env_accepts_aliases`（`@parametrize("value", ["1","true","TRUE","yes","Yes"])`）
   fixture 相同、函数体相同，前者恰是后者 `value="1"` 的那个实例，**被完全包含**。

同时**两个已知的「正当重复」形态都验证到了**：

- `test_wan_family_consistency.py`（故意的组合矩阵）：**k=25 只报 1 对，k=50 及以上一对都没有**。
  对照 #2265，该单文件曾贡献 cov-context L4 候选的 78/921（8.5%）。
  原因是组合矩阵写在 `@parametrize` 的**数据**里而不是重复的**代码**里，token 级检测器结构性看不见它。
- #2265 那对「30 行 setup 逐字相同、差别正是被守契约」的用例
  （`test_execute_video_task.py::..._end_frame_image_passed_to_generator` 与 `..._bare_filename_resolves_via_default_dir`）：
  jscpd 在 k=50 报了它们（L832-841 与 L861-870、L846-856 与 L875-885），
  但每段只覆盖各自函数体的约 40%，**没有进入 cover 阈值以上的判定层**，过滤器把它正确地留在了外面。

---

## 3. 召回缺口（核心产出）：#2266 判据的召回率 = 3/5 = 60%

### 3.1 #2266 的三组存量在 jscpd 里几乎找不到

`dup_body.py`（原样取自 `research/redundant-tests`）在当前 HEAD 上复现：
测试函数 **8806**、体规范化后相同的组 20、跨文件 5、含 parametrize 12、**严格组 3（涉及 6 个函数）**，与 #2265 一致。

把这三组拿去比对各档 jscpd 报告。判定用**宽松重叠**（只要某 clone 对的一端与 A 的函数区间有交集、另一端与 B 有交集就算命中），
避免因 jscpd 贪心外扩越过函数边界而误判为漏检：

| min-tokens | 组 1 config_resolver | 组 2 script_structure_validator | 组 3 test_auth |
| ---: | :-: | :-: | :-: |
| 10 | 未命中 | 未命中 | 未命中 |
| **25** | **命中** | 未命中 | 未命中 |
| 50 | 未命中 | 未命中 | 未命中 |
| 75 / 100 / 150 / 200 | 未命中 | 未命中 | 未命中 |

**jscpd 在任何档位都至多找到 3 组里的 1 组。** 三条原因，全部是结构性的：

1. **函数体太短，够不着任何可用阈值。** 组 2 的整个函数体是
   `assert validate_script_structure(_drama()).valid`，约 8 个 token，比最低档 k=10 还小；
   两端的 `def` 行与类名不同，jscpd 无法向外延伸出连续块。
2. **jscpd 的贪心最大化会越过函数边界，把窗口挪走。** 组 1 在 k=25 报的是 `[81:87]` 对 `[121:127]`（正好两个函数体），
   但在 k=10 变成 `[74:80]` 对 `[121:127]`，起点退到了上一个函数里，**函数级映射反而丢失**。
   **降低 min-tokens 不是单调增召回的**：k=10 比 k=25 少命中一组。
3. **同文件的邻近块会互相吸收。** 组 3 所在的 `test_auth.py` 在 k=10 只有 4 对同文件 clone，
   目标行 106-109 与 269-272 一次都没被覆盖，被更长的相邻块占掉了。

### 3.2 反向：jscpd 找到了 #2266 结构性找不到的 2 条

上面第 2 节的 A 类三条里，只有第 2 条是 #2266 存量。另两条 #2266 判据**必然漏**：

- `test_returns_204`：函数体不是逐字相同（多一行断言、字面量不同），`dup_body.py` 压根不会把它们分到同一组。
- `test_file_handler_disabled_by_env`：函数体 `setenv(..., "1")` 与 `setenv(..., value)` 也不是逐字相同；
  **且即便相同，#2266 的边界 2「排除带 parametrize 的组」也会把它排掉**，
  而这里 parametrize 的那一侧恰恰**包含**了另一侧，属于真·可删。

### 3.3 召回率

以「本图迄今在 `tests/` 里已确认为真的可删重复」为分母（两条路径的并集，5 条）：

| 检测器 | 命中 | 召回 | 精度 | 采集成本 |
| --- | ---: | ---: | ---: | ---: |
| #2266 AST 判据（逐字同体 + 同文件 + 双方无 parametrize） | 3 / 5 | **60%** | 3/3 = **100%** | 1.1 s |
| jscpd 整体近似重复层（k=25，cover 阈值 0.8） | 3 / 5 | **60%** | 3/63 = **4.8%** | 1.6 s |
| 交集 | 1 | — | — | — |

**把 #2265 明列的「召回未知」变成一个数：#2266 的判据召回约 60%（3/5）。**
另外 40%（2/5）不是「语义等价但写法不同」的高阶重复，而是**两种朴素的、判据边界主动排除的形态**：

- **断言真子集**（同路径、一条的断言集包含另一条），#2266 只比「相等」，不比「包含」；
- **parametrize 侧包含非 parametrize 侧**，#2266 的边界 2 把所有含 parametrize 的组一律划成「可合并而非可删」，
  该假设在「参数表包含了另一条用例的全部输入」时不成立。

两个检测器**几乎正交**（交集仅 1/5），互相都不能替代。

---

## 4. 值得开执行票的批量形态：有，而且是本票体量最大的产出

### 形态一：跨文件重复的局部替身 / 辅助函数（最大的一块）

k=50 下，**跨文件、且两端宿主都不是 test 函数**的 clone 对有 **85 对**，
连通后是 **46 个族、125 个符号、涉及 99 个测试文件**（`raw/cross-file-helpers-k50.txt`、`raw/helper-groups-k50.txt`）。
最大的几族：

| 规模 | 形态 |
| ---: | --- |
| 9 | 各类 checkpoint / facts JSON 构造器（`_worker_storyboard_checkpoint`、`_storyboard_checkpoint_json`、`_currency`、`_facts` 等） |
| 5 | `_FakePM::save_project`，在 asset_router_factory / characters / products / props / scenes 五个 router 测试里逐字重复 |
| 5 | `_read_json`，在 4 个 project_migrations 测试加 1 个 router 测试里重复 |
| 4 | `_write_text` / `_write_bytes`，archive 相关 4 文件 |
| 4 | `_status` / `_Planner::get_plan`，workflow / remote_mcp / tool_runtime 4 文件 |
| 4 | alembic 迁移测试的 `revisions` 解析器（**其中一份就在同目录的 `conftest.py` 里**） |
| 4 | 建 TestClient 的 `_client` / `_app` 家族 |

这直接对上 CONTRIBUTING「共享设施」的既有约定
（fakes / factories / 专题模块的公开符号须被 2 个以上测试文件使用；同一实体的 fixture 在 3 个以上文件重复定义时上提 conftest）。
**现有 `audit_tests.py` 检不出这一形态**，它校验的是「共享模块里的符号有没有被 2 个以上文件用」，
而不是「同一段实现被复制进了 N 个文件」。这是既有闸门覆盖面上的一个真实缺口。
最刺眼的一条：`test_alembic_custom_provider_capability_overrides.py::revisions` 与
**同目录 `conftest.py::migration_revisions` 逐字重复**，可直接消费现成 fixture。

### 形态二：可合并成一条参数化的用例族（27 族，占 63 的 42.9%）

清单见 `raw/verdicts-k25.md` 的 B 类。按可操作性再分三档：

- **纯 `@parametrize` 合并**（同断言，只差输入与期望值）：族 #1 #2 #3 #4 #5 #6 #17 #22 #24 #31 #32 #35 #38 #42 #54 #62，共 **16 族**。
  最干净的是 #6（`test_assistant_routes.py` 三个端点都返回 410，只差 URL 后缀）
  与 #38（`test_quality_propagation.py` 两条各带 30 余行逐字相同的 MagicMock 装配，只差 `SettlementInput(quality=...)`）。
- **断言互补、setup 重复，宜并成一条用例**：族 #26 #36 #47 #50 #57 #59，共 **6 族**
  （典型：`test_name_and_model` 与 `test_capabilities` 各自重建一次 backend）。
- **双方都已 parametrize、函数体逐字相同，宜并参数表或抽 helper**：族 #13 #18 #19 #40 #49，共 **5 族**。

### 形态三：单文件内的重 setup（不建议做成批量票）

`test_execute_reference_video_task.py` 一个文件占 k=50 全部重复行的 8.3%（1008/12108），
最大的 clone 块 201 token / 31 行也在这里。但这类是**用例内部**的样板，
处置方式是抽 fixture / builder，属于该文件的局部重构，收益和风险都局限在一个文件里，
按文件开票即可，不构成跨文件的批量形态。

---

## 计划外的发现

**① jscpd 对「故意的组合矩阵」结构性免疫，这正是 cov-context 路线翻车的地方。**
`test_wan_family_consistency.py` 在 cov-context 的 L4 候选里占 78/921（8.5%），在 jscpd k=50 及以上是 **0**。
根因是组合矩阵表达为 `@parametrize` 的**数据**，不是重复的**代码**，token 级检测器只看代码。
这条对地图 Notes 里「引用 1.39:1 时须先扣掉组合矩阵测试」是一个独立佐证：
**从代码重复的角度看，组合矩阵文件根本不重复。**

**② 「降低 min-tokens 换召回」不成立。** k=10 比 k=25 更吵（22.95% 对 16.34% 重复行），
却在 #2266 的三组存量上**少命中一组**：贪心最大匹配在低阈值下会把窗口向外扩到相邻函数，反而破坏函数级定位。
任何后续想用 jscpd 找短用例重复的尝试都会撞上这堵墙，**它的下限不是阈值定的，是「最大化连续块」这个算法定的。**

**③ #2266 的边界 2（排除含 parametrize 的组）有一个可检查的反例族。**
当参数表**包含**了另一条非参数化用例的全部输入时，被包含的那条是真·可删，
而现行边界会一律把它划成「可合并而非可删」。存量至少 1 条（`test_logging_persistence.py`）。
这不是要求推翻边界 2，边界 2 对「差异在装饰器里」的 12 组仍然正确；
是说它可以收窄成「**排除双方都带 parametrize 的组**」，并对「一侧带、且参数表覆盖另一侧输入」单独判。
本票不改 #2266，只登记。

**④ 生产代码的重复率只有测试的六分之一。** 顺手扫了一遍 `lib` 加 `server`（k=50）：
401 文件 / 131,822 行 / 137 对 clone / 1455 重复行 = **1.10%**，对比 `tests/` 的 **6.48%**。
**生产代码不属本图，此处不展开，需要时另起一张图。**

**⑤ jscpd 的成本结构与 `audit_tests.py` 完全同量级，但这不改变闸门结论。**
1.59 s 全量、零配置、`pnpm dlx` 一次性可跑。
但票面已判死的三条理由都不是成本问题（阈值型工具必然要忽略清单 / CI `test-lint` 只装 Python / 闸门零容忍无豁免），
本票**没有**重新评估闸门，也不提任何闸门提案。

---

## 未确认的点

1. **逐条判定只覆盖了「整体近似重复」这一层（63 族 / 136 函数），它只占 k=25 全部 3809 对的 2.0%。**
   剩下的 3700 多对只做了 15 条随机抽样（k=50 层，0 条可删）。若那 98% 里藏着可删项，
   本票的可处置率 4.8% 偏低、#2266 的召回 60% 偏高。
2. **cover 阈值 0.8 没有做敏感性分析。** 它在短用例上会把「共享一段 setup」误判成整体重复（实测 5/63 是 D 类），
   在长用例上会漏掉「共享 40% 主体」的对（#2265 的 end_frame 那对就是这样被排除的）。
   0.6 或 0.9 会怎样，未跑。
3. **召回率的分母是「已确认真阳的并集」（5 条），不是真实 ground truth。**
   两条路径都可能整体性地漏掉某类重复（例如跨文件的整体重复：`dup_body.py` 按定义排除，
   jscpd 在 k=50 及以上一条跨文件整体重复也没报）。60% 是**对已知集合的召回**，不是绝对召回。
4. **只跑了 jscpd 默认 mode。** `--mode weak/strict`（是否跳过注释 token）没有对照跑；
   Python 的 docstring 是字符串不是注释，两种 mode 下都会计入，预计影响不大但未实测。
5. **未验证 jscpd 结果的跨版本稳定性。** 只在 5.1.1 上跑过一次；该版本是 Rust 重写实现，
   与更早的 JS 版 jscpd 在 clone 边界上是否一致，未核。
6. **B 类 27 族的合并可行性只判到「同形可合并」，没有逐族写出合并后的形状。**
   若真开执行票，须逐族确认合并不会削弱断言或丢失用例名承载的契约信息
   （#2266 已警示「用例名声称守某契约点」这一类信息不能在合并中丢失）。

---

## 关于 `raw/` 的说明

`raw/*/jscpd-report.json` 已用 `python3 research/jscpd-survey/slim_raw.py` 去掉 `fragment` 正文
（行号、token 数、统计全部保留），`raw/mapped-*.json` 只留 `families`。
本目录所有脚本都能在瘦身后的数据上照常复跑；唯一用到 `fragment` 正文的 `sample_partial.py`
其抽样输出已完整存为 `raw/sample-partial-k50.txt`。要拿回完整报告，重跑 `run_jscpd.sh` 即可（1.6 s）。
