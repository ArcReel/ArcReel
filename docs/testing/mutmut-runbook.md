# mutmut 变异测试 runbook

本手册用于指导在本地挑选变异测试批次、复核运行结果，以及对比测试改造前后的表现。工具版本与配置见 [`pyproject.toml`](../../pyproject.toml) 中的 `[tool.mutmut]` 与 `mutation` 依赖组。

变异测试按批次在本地运行。变异得分与测试取舍原则以[测试规范](../standards/testing.md)为准。

**存活只是把测试送上审查台，而不是删除依据。** 当存活 mutant 落在已有测试覆盖的代码上时，按 `docs/standards/testing.md` 中「每条用例保护一个可观察的契约」的处置建议逐条判定。以下情形不作处置：

- 不改既有用例输入、仅加强断言无法杀死，必须更换输入才能杀死的 mutant（属于覆盖补偿）。
- 等价变异体（源码改动但行为不变）。
- 落在无测试覆盖代码上的 mutant。

## 1. 环境

mutmut 位于独立依赖组 `mutation` 中，默认 `uv sync` 不会安装：

```bash
uv sync --group mutation
```

变异后的模块顶部会 import mutmut 的 trampoline，因此它必须安装在 `.venv` 中，使用 `uv run --with mutmut` 无法覆盖后续复核步骤。跑完一批后若想清理，重新执行 `uv sync` 即可。

## 2. 跑一批

### 2.1 选模块

每批挑选约 8 个尚未走查的模块，挑选前先核对[既有批次](#7-既有批次)，并在本批议题中登记模块列表、基线提交和结果位置。

- **准入**：`server/routers` 整目录不选；其他目录按文件看，被装饰器跳过的行占比不低于 50% 的文件不选。mutmut 3.7.0 对带装饰器的函数整棵子树不生成 mutant（仅单个 `@staticmethod` / `@classmethod` 豁免，带装饰器的类整体跳过，不可配置），路由函数与 `@tool` 闭包这类形态跑了也几乎不产出 mutant。跑一次普查列出不合准入的文件：

  ```bash
  uv run python scripts/decorator_census.py --per-file --min-skip 50
  ```

  不带参数时会按目录汇总跳过行占比，便于判断哪些目录整体值得优先挑选。

- **优先级**：从尚未走查、测试较多且规模较大的模块选。现有候选区域包括 `server/services`、`lib/backends/video_backends`、`lib/custom_provider` 与 `lib/script/reference_video`。
- **终点**：不设。每批跑完算可处置率（判为 ③ 值得保护的存活 mutant 数 / 存活 mutant 数），连续两批明显下降就停。

### 2.2 跑一轮

1. 执行 `git fetch` 后从 `origin/main` 切出新分支，并确认工作树已包含上一批合入的 PR。如果基线从过期的本地 `main` 切出，上一批改造解决的 mutant 会在本批「复活」，白白浪费一轮复核。
2. 在 `[tool.mutmut]` 的 `only_mutate` 中填入本批模块。留空会变异 `source_paths` 全域，一轮耗时数小时。更换模块后，先将需要的基线另存，再按[同批复跑与更换模块的规则](#5-改造后的三层验收)清理旧的 `mutants/`（含 stats）。
3. 运行：

   ```bash
   uv run mutmut run
   ```

   并行度默认取 CPU 数，不用降。各会话的临时目录由 `tests/mutmut_plugin.py`（经 `[tool.mutmut] pytest_add_cli_args` 加载）设为私有，临时库回收由 `tests/conftest.py` 按创建者进程守卫，并行子进程之间不共享清理动作。
4. 保存与基线提交对应的 `mutants/**/*.meta`，路径应独立于 `mutants/`。改造后用这份快照做第 5 节的比对。

跑完后记得将 `only_mutate` 还原为注释，`mutants/` 已加入 `.gitignore`。

**凡是读取源码文本而非 import 模块的用例，都必须在 `pytest_add_cli_args` 中按 nodeid 使用 `--deselect` 排除，切勿整文件 `--ignore`。** `mutants/` 中的源文件同时包含所有 mutant 变体（每个字符串字面量都会多出 `XXfooXX` / `FOO` 两份），任何扫描 `lib/`、`server/` 源码字面量并与登记表比对的用例都会在 stats 阶段报「未登记」，导致整轮无法启动。而 `--ignore` 的粒度是整个文件：同文件中的行为测试也会被一并跳过，它们独家覆盖的 mutant 就会被记为存活（`test_task_failure_capability.py` 中三个扫描用例之外的 100 余个用例就是典型例子）。目前该文件已排除的是 `test_capability_codes_registered_no_drift` / `test_no_unscannable_capability_construction_sites` / `test_capability_construction_sites_supply_every_template_param`；后续新增排除时，须在 `[tool.mutmut]` 注释中写明该用例独家能杀什么、为何排除不会漏杀（判据：排除只会多出假存活，不会藏住假杀死）。

## 3. 读结果

**先核对每个模块的 mutant 数量级。** 通常每千行源码约有 1000 个 mutant，数量明显偏少（例如不到十分之一）的模块并非测得极好，而是其函数未被任何用例关联：mutmut 会将这类模块静默归零，终端也不报错。遇到这种情况应将该模块从本批剔除、不作判定；另外，`only_mutate` 中模块名拼写错误时同样会产出 0 个 mutant，需先核对拼写。

**从 `.meta` 文件统计，不要依赖终端汇总。** 每个源模块在 `mutants/` 中都有一份 `<模块>.py.meta`，其中的 `exit_code_by_key` 记录了「mutant 名 → pytest exit code」。mutmut 的终端汇总只统计它认识的几个 exit code，段错误（−11）这类退出码不会落入任何计数器，#2257 就曾因此漏掉两个。

| exit code | mutmut 的解释 | 本仓库的处理 |
| --- | --- | --- |
| 1 | killed | killed |
| 0 | 存活（🙁） | 存活，进入判定 |
| 36 / −24 / 24 / 152 / 255 | 超时（⏰） | 按第 4 节新进程复核 |
| 3 | mutmut 把 pytest 内部错误当 killed | 复核 |
| 其他（含 −11 段错误、None 未检查） | 可疑或未记 | 复核 |

判定规则只有一条：**exit code 既不是 1 也不是 0 的一律复核**，复核确认存活后才进入判定。0 表示 pytest 全部通过，本身即确认存活，无需复核。

## 4. 超时的新进程复核

超时属于罕见兜底，并非正常现象。此前的成因在于 fork：mutmut 为每个 mutant fork 子进程运行测试，父进程模块级 engine（`lib.db.engine`）连接池中残留的 aiosqlite 连接被子进程继承，但驱动它的工作线程并未随 fork 带过去，导致子进程中首个使用该 engine 的用例永远等不到查询结果，耗尽预算后被记为超时。目前 `tests/conftest.py` 已通过 `os.register_at_fork` 在子进程中丢弃池内连接（`dispose(close=False)`），首批 8 模块 8 路全量复跑的超时数为 0。若仍出现超时，不可直接算作 killed，而应在新进程中只跑关联用例再确认一次，每个耗时约 3 到 11 秒。

```bash
# 在项目根跑：tests-for-mutant 读 mutants/mutmut-stats.json，在 mutants/ 里跑会报 Failed to load stats
uv run mutmut tests-for-mutant lib.speech.speech_rate.x_estimate_spoken_seconds__mutmut_8 > nodeids.txt

# 在 mutants/ 里跑，用 .venv 的解释器，不要用 uv run（见第 6 节）
cd mutants && MUTANT_UNDER_TEST=lib.speech.speech_rate.x_estimate_spoken_seconds__mutmut_8 \
  ../.venv/bin/python -m pytest -x -q @../nodeids.txt
```

pytest 原生支持 `@文件` 读参数，一行一个、原样保留空格和引号，本套件 180 多个含空格或引号的参数化 nodeid 都能过。不要用 `$(cat …)` 或裸 `xargs`：前者在 zsh 下整串成一个参数、pytest 静默跑 0 个用例，后者会把带空格的 nodeid 拆开。

判读标准：**1 failed 即为 killed；全部通过即为存活。** 复核确认 killed 的（包括段错误等两头不落的情况），需在第 2 节保存的基线副本中将该 mutant 的 exit code 改为 1，以便第 5 节将其归入「基线 killed」护栏；确认存活的保持原样。首批 616 个 mutant 中需要复核的仅有 2 个段错误，复核后均确认 killed。

## 5. 改造后的三层验收

改完断言后，对同一批模块**全量复跑**一次 `mutmut run`，再与基线比对。由于 `mutmut run` 不会重跑已有结果的 mutant，而 mutant 清单本身又存放在 `.meta` 里，只删 `.meta` 会导致得到 0 个 mutant。同一批模块复跑的做法是：先将 `mutants/mutmut-stats.json` 移出，删除整个 `mutants/`，再将其放回新建的 `mutants/`，这样不必重收 stats（可省约 5 分钟）。若更改了 `only_mutate` 的模块集，则须连同 stats 一起删除整个 `mutants/`——因为 stats 只记录被变异模块的函数命中，新增模块会被整批记为 🫥 无关联用例。

```bash
uv run python scripts/mutmut_compare.py \
  --baseline /path/to/saved-baseline \
  --current mutants \
  --reworked reworked.txt \
  --equivalent equivalent.txt
```

`reworked.txt` 每行写一个本次改造针对的 mutant 名；`equivalent.txt` 为可选参数，每行写一个判定为等价变异体的 mutant 名。脚本会输出三层对照表与待复核清单：exit code 0 表示通过，1 表示未通过或存在待复核项，2 表示输入不一致（如基线与本轮 mutant 名集合不同、名单中的 mutant 不在基线中等）。存在待复核项时不能判为通过：须先按第 4 节复核，确认 killed 的将本轮 `.meta` 中该 mutant 的 exit code 改为 1，确认存活的改为 0，再重新比对得出最终结论。

| 层 | 通过条件 | 未通过怎么办 |
| --- | --- | --- |
| 本次改造针对的 mutant | 全部 exit code 1 | 变超时 → 按第 4 节复核，1 failed 即 killed；仍存活 → 四路分诊（见下） |
| 基线 killed 的 mutant | 没有一个变成 exit code 0 | 变超时 → 新进程复核，1 failed 即护栏成立，只有全部通过才是回退；回退 = 改坏了别的用例 |
| 基线存活且本次未改造的 mutant | 被杀死只登记；等价变异体的 exit code 仍是 0 | 判为等价变异体的被杀死 → 先按第 4 节新进程复核，但去掉 `-x`，同一命令连跑 3 次。失败点按 traceback 里失败断言所在的文件与行号计，不按用例 nodeid 计：一个用例只会停在第一个失败的断言，nodeid 相同分不出是新加还是既有断言。3 次失败点相同且全是本次新加的断言 → 是判定错了，不是这轮不可信，改判并写进判定表（补的断言若声称超出判定表那一句则收窄断言、维持等价）；3 次失败点相同但含既有断言 → 同样改判：基线存活只说明基线那轮 stats 没把这条用例关联到该 mutant，不是等价；3 次全部通过，或失败点在轮次之间不同 → 整轮作废：等价变异体不可能被杀，说明这轮子进程的结果不可信（如异常退出被记成 killed）；等价变异体变超时或段错误 → 按第 4 节复核，1 failed 即被杀死。改判或改 `.meta` 之后都要再比对一次拿最终结论。其余未改造 mutant 的异常 exit code 不影响结论 |

确认改造没有触碰生产代码：运行 `git diff --name-only origin/main -- lib/ server/`，结果应为空。生产代码变动会改变变异体编号，使前后两次结果失去可比性。

**四路分诊**（改造后目标 mutant 仍存活时，一次性判定，补的断言不撤）：断言没写到位、断言没被执行 → 继续修；实际要换输入才能杀死 → 停手，依据是「不做覆盖补偿」；实际是等价变异体 → 停手。

只跑部分模块无法校验第二层护栏，复跑必须覆盖整批模块。

### 5.1 一批模块拆成多张改造票

一批模块的存活 mutant 常拆成多张改造票跨会话合入，它们共用第 2 节保存的同一份基线，由各票分别验收：

- **先走曳光弹**：每批改造先挑 2 条，单独走完「改断言 → 全套闸门 → 全量复跑 → 三层验收 → PR 合入」，通过后再批量改造其余条目。曳光弹的两条与后续批量票共用同一基线，批量票验收时它们落在第三层「基线存活且本次未改造」，被杀只需登记。
- `--reworked` 只填本票清单；`--equivalent` 填整批的等价变异体清单（每张票都必须校验其零被杀）。
- 先合入的票已经杀死的目标，在后续票中落在第三层「基线存活且本次未改造」，被杀只登记，不算异常。
- 每票切分支前执行 `git fetch`，并核对基线涉及的模块在 `origin/main` 上自基线提交以来零变动：`git diff --stat <基线提交> origin/main -- <模块列表>` 应为空。改造 PR 自身不会改动这些模块（硬门禁），但 `main` 上的其他提交可能会改到。
- 若模块已被其他提交修改，脚本会因「同名函数源码哈希不同」拒绝比对，此时旧基线与判定表对该模块全部作废：mutant 名按函数内序号编号，源码一变序号就会整体错位，原判定行将无法对应。处置方式是**对该模块重跑一轮基线并重新判定其存活 mutant**（其余未变动模块继续沿用旧基线；若 `only_mutate` 更换了模块集，须连同 stats 一起删除），以此更新基线快照。即便改动仅落在个别函数也一样：mutmut 不保证其他函数的编号不变，因此不做局部续用。

## 6. 已知陷阱

- **新增的测试文件不触及任何被变异函数时，增量 stats 会中止。** mutmut 检测到新用例只对它们跑一次 stats，若这批用例没碰到任何 mutant，报 `Stopping early, because we could not find any test case for any mutant` 退出。删 `mutants/mutmut-stats.json` 走全量 stats。
- **不要在 `mutants/` 里跑 `uv run`。** uv 会按那份 `pyproject.toml` 副本另建一个环境，mutmut 不在里面，变异模块顶部的 trampoline import 直接 `ModuleNotFoundError`。用 `../.venv/bin/python`。
- **`tests-for-mutant` 只在项目根可用。** 它读 `mutants/mutmut-stats.json`，在 `mutants/` 里跑找不到。
- **`timeout_multiplier` 不要调小。** 调小不省时间，只会让已证实的真存活被记成 killed，把无效测试藏起来。
- **−11（段错误）成批出现即整轮作废，不要逐个复核。** 已知的一种成因在 macOS：环境里没有 `*_proxy` 变量时，`urllib.request.getproxies()` 经 `_scproxy` 调 SystemConfiguration 读系统代理，该框架在 fork 出的多线程子进程里直接段错误，凡构造 `openai.OpenAI` / `httpx.Client` 的用例都中招（第二批 2186 个里 577 个），而同一个 mutant 在新进程里复核却正常。`tests/mutmut_plugin.py` 已把这两个入口换成常量实现；再成批出现就用 `PYTHONFAULTHANDLER=1 uv run mutmut run <mutant>` 单跑一个看崩溃栈，别当成负载或偶发。判据：−11 只该零星出现（首批 616 个里 2 个）。
- **测试选集走全量。** 只跑 `tests/unit` 快 5.5 倍，但约 45% 的存活是假的，每个都要复核一次全量套件，总账更贵。

## 7. 既有批次

| 批次 | 模块与结果入口 |
| --- | --- |
| 第一批 A、B 组 | [A 组试跑 #2253](https://github.com/ArcReel/ArcReel/issues/2253)、[B 组及汇总 #2257](https://github.com/ArcReel/ArcReel/issues/2257) |
| 第二批池 1（供应商接入） | [#2297](https://github.com/ArcReel/ArcReel/issues/2297)。当时的 `lib/{text,video,image}_backends/` 路径现在位于 `lib/backends/` 下 |
| 第二批池 2（SDK 工具） | [#2298](https://github.com/ArcReel/ArcReel/issues/2298)。11 个模块中有 9 个因 `@tool` 闭包被跳过而未产出变异体 |

历史议题中的命令与分支名仅作为当时的记录。执行新批次或需要已找不到的基线时，请按本手册重新生成和复核。

首批 8 模块共 616 个变异体，在 8 路并行下约 10 分钟跑完，首次全量 stats 约 5 分钟。规模估算可参考这份实测数据，当机器、模块或测试选集变化时需重新测量。
