# mutmut 变异测试 runbook

跑一批变异测试、复核结果、改造测试后验收的操作步骤。规范（信号不闸门、存活 mutant 如何接入三步处置、三层验收判据）在 [CONTRIBUTING「变异测试」](../../CONTRIBUTING.md#变异测试)，本文只讲怎么跑。配置在 `pyproject.toml` 的 `[tool.mutmut]`，配置项的取舍理由写在那里的注释里。

## 1. 环境

mutmut 在独立依赖组 `mutation` 里，不随默认 `uv sync` 安装：

```bash
uv sync --group mutation
```

变异后的模块顶部会 import mutmut 的 trampoline，所以它必须装在 `.venv` 里，`uv run --with mutmut` 覆盖不了后面的复核步骤。跑完一批想清掉时再 `uv sync`。

## 2. 跑一批

1. `git fetch` 后从 `origin/main` 切分支，并核对树里已含上一批合入的 PR。基线若从过期的本地 `main` 切出，上一批改造针对的 mutant 会在本批「复活」，白花一轮复核。
2. 在 `[tool.mutmut]` 的 `only_mutate` 填本批模块。留空会变异 `source_paths` 全域，一轮数小时。
3. 跑：

   ```bash
   uv run mutmut run
   ```

   并行度默认取 CPU 数，不用降。各会话的临时目录由 `tests/mutmut_plugin.py`（经 `[tool.mutmut] pytest_add_cli_args` 加载）设为私有，临时库回收由 `tests/conftest.py` 按创建者进程守卫，并行子进程之间不共享清理动作。
4. 跑完把 `mutants/**/*.meta` 复制到一个不会被下一次 `mutmut run` 覆盖的地方，这是第 5 节验收要用的基线。首批放在 `research/mutmut-batch-1` 分支的 `baseline/` 下。

`only_mutate` 跑完记得还原成注释，`mutants/` 已在 `.gitignore`。

**凡读源码文本而不是 import 模块的用例，都要在 `pytest_add_cli_args` 里按 nodeid `--deselect`，不要整文件 `--ignore`。** `mutants/` 里的源文件同时含所有 mutant 变体（每个字符串字面量都多出 `XXfooXX` / `FOO` 两份），任何扫 `lib/` `server/` 源码字面量再比对登记表的用例都会在 stats 阶段报「未登记」，整轮起不来。`--ignore` 的粒度是文件：同文件里的行为测试会一并消失，它们独家覆盖的 mutant 就记成存活（`test_task_failure_capability.py` 三个扫描用例之外的 100 余个用例就是这种情况）。已排除的是该文件的 `test_capability_codes_registered_no_drift` / `test_no_unscannable_capability_construction_sites` / `test_capability_construction_sites_supply_every_template_param`；新加排除时在 `[tool.mutmut]` 注释里写明它独家能杀什么、为何排除不漏杀（判据同 `test_skill_script_path_guards.py`：排除只多出假存活，不藏假杀死）。

## 3. 读结果

**从 `.meta` 文件算，不读终端汇总。** 每个源模块在 `mutants/` 里有一份 `<模块>.py.meta`，其中 `exit_code_by_key` 是「mutant 名 → pytest exit code」。mutmut 的终端汇总只枚举它认识的几个 exit code，段错误（−11）之类一个计数器都不落，#2257 就漏过两个。

| exit code | mutmut 的解释 | 本仓库的处理 |
| --- | --- | --- |
| 1 | killed | killed |
| 0 | 存活（🙁） | 存活，进入判定 |
| 36 / −24 / 24 / 152 / 255 | 超时（⏰） | 按第 4 节新进程复核 |
| 3 | mutmut 把 pytest 内部错误当 killed | 复核 |
| 其他（含 −11 段错误、None 未检查） | 可疑或未记 | 复核 |

判据只有一条：**exit code 既不是 1 也不是 0 的一律复核**，复核确认存活的才进入判定。0 是 pytest 全部通过，本身就是确认存活，不用复核。

## 4. 超时的新进程复核

超时是罕见兜底，不是常态。曾经的成因是 fork：mutmut 按 mutant fork 子进程跑测试，父进程模块级 engine（`lib.db.engine`）池里残留的 aiosqlite 连接被子进程继承，而驱动它的工作线程没有一起过来，子进程首个走该 engine 的用例就永远等不到查询结果、耗满预算记成超时。`tests/conftest.py` 用 `os.register_at_fork` 在子进程里丢弃池中连接（`dispose(close=False)`），首批 8 模块 8 路全量复跑超时为 0。仍出现的超时不算 killed，要在新进程里只跑关联用例再确认一次，每个 3 到 11 秒。

```bash
# 在项目根跑：tests-for-mutant 读 mutants/mutmut-stats.json，在 mutants/ 里跑会报 Failed to load stats
uv run mutmut tests-for-mutant lib.speech_rate.x_estimate_spoken_seconds__mutmut_8 > nodeids.txt

# 在 mutants/ 里跑，用 .venv 的解释器，不要用 uv run（见第 6 节）
cd mutants && MUTANT_UNDER_TEST=lib.speech_rate.x_estimate_spoken_seconds__mutmut_8 \
  ../.venv/bin/python -m pytest -x -q @../nodeids.txt
```

pytest 原生支持 `@文件` 读参数，一行一个、原样保留空格和引号，本套件 180 多个含空格或引号的参数化 nodeid 都能过。不要用 `$(cat …)` 或裸 `xargs`：前者在 zsh 下整串成一个参数、pytest 静默跑 0 个用例，后者会把带空格的 nodeid 拆开。

判读：**1 failed 即 killed；全部通过即存活。** 复核确认 killed 的（含段错误之类两头不落的），在第 2 节保存的基线副本里把该 mutant 的 exit code 改成 1，这样第 5 节会把它们归入「基线 killed」那层护栏；确认存活的保持原样。首批 616 个 mutant 里要复核的只有 2 个段错误，复核后确认 killed。

## 5. 改造后的三层验收

改完断言后，对同一批模块**全量复跑**一次 `mutmut run`，然后比对。`mutmut run` 不重跑已有结果的 mutant，而 mutant 清单本身又存在 `.meta` 里，只删 `.meta` 会得到 0 个 mutant。同一批模块复跑：把 `mutants/mutmut-stats.json` 挪出来、删掉整个 `mutants/`、再把它放回新建的 `mutants/`，stats 不用重收（省 5 分钟）。换了 `only_mutate` 的模块集则整个 `mutants/` 连 stats 一起删：stats 只记录被变异模块的函数命中，新加的模块会整批记成 🫥 无关联用例。

```bash
uv run python scripts/mutmut_compare.py \
  --baseline research/mutmut-batch-1/baseline \
  --current mutants \
  --reworked reworked.txt \
  --equivalent equivalent.txt
```

`reworked.txt` 一行一个本次改造针对的 mutant 名；`equivalent.txt` 可选，一行一个判定为等价变异体的 mutant 名。脚本输出三层表与待复核清单，exit code 0 通过、1 未通过或有待复核项、2 输入不一致（基线与本轮 mutant 名集合不同、名单里的 mutant 不在基线等）。有待复核项时不判通过：按第 4 节复核后，确认 killed 的把本轮 `.meta` 里该 mutant 的 exit code 改成 1，确认存活的改成 0，再比对一次拿最终结论。

| 层 | 通过条件 | 未通过怎么办 |
| --- | --- | --- |
| 本次改造针对的 mutant | 全部 exit code 1 | 变超时 → 按第 4 节复核，1 failed 即 killed；仍存活 → 四路分诊（见下） |
| 基线 killed 的 mutant | 没有一个变成 exit code 0 | 变超时 → 新进程复核，1 failed 即护栏成立，只有全部通过才是回退；回退 = 改坏了别的用例 |
| 基线存活且本次未改造的 mutant | 被杀死只登记；等价变异体的 exit code 仍是 0 | 判为等价变异体的被杀死 → 整轮作废：等价变异体不可能被杀，说明这轮子进程的结果不可信（如异常退出被记成 killed）；等价变异体变超时或段错误 → 按第 4 节复核，1 failed 即被杀死。其余未改造 mutant 的异常 exit code 不影响结论 |

同时有一道独立硬门：`git diff --name-only origin/main` 不得含 `lib/` 与 `server/`。生产代码一变，`mutants/` 重生成、mutant 名错位，比对本身不成立。

**四路分诊**（改造后目标 mutant 仍存活时，一次性判定，补的断言不撤）：断言没写到位、断言没被执行 → 继续修；实际要换输入才能杀死 → 停手，依据是「不做覆盖补偿」；实际是等价变异体 → 停手。

只跑部分模块查不到第二层，复跑取整批模块。

### 5.1 一批模块拆成多张改造票

一批模块的存活 mutant 常拆成几张改造票跨会话合入，它们共用第 2 节保存的同一份基线，各票分别验收：

- `--reworked` 只填本票的清单；`--equivalent` 填整批的等价变异体清单（每票都要查它零被杀）。
- 先合入的票已杀死的目标，在后面的票里落在第三层「基线存活且本次未改造」，被杀只登记，不算异常。
- 每票切分支前 `git fetch` 并核对基线的模块在 `origin/main` 上自基线提交以来零变动：`git diff --stat <基线提交> origin/main -- <模块列表>` 为空。改造 PR 自身不会动这些模块（硬门），但 `main` 上别的提交会。
- 模块被别的提交改了时，脚本会以「同名函数源码哈希不同」拒绝比对，此时旧基线与判定表对该模块都作废：mutant 名按函数内的序号编号，源码一变序号整体错位，判定行对不上任何 mutant。处置是**对该模块重跑一轮基线并重新判定它的存活 mutant**（其余未变动的模块继续用旧基线；`only_mutate` 换了模块集要连 stats 一起删），不要 rebase 研究分支——研究分支存的是结果快照，rebase 改不了快照里的 mutant 名。改动只落在个别函数时也一样：mutmut 不保证其他函数的编号不变，不做局部续用。

## 6. 已知陷阱

- **新增的测试文件不触及任何被变异函数时，增量 stats 会中止。** mutmut 检测到新用例只对它们跑一次 stats，若这批用例没碰到任何 mutant，报 `Stopping early, because we could not find any test case for any mutant` 退出。删 `mutants/mutmut-stats.json` 走全量 stats。
- **不要在 `mutants/` 里跑 `uv run`。** uv 会按那份 `pyproject.toml` 副本另建一个环境，mutmut 不在里面，变异模块顶部的 trampoline import 直接 `ModuleNotFoundError`。用 `../.venv/bin/python`。
- **`tests-for-mutant` 只在项目根可用。** 它读 `mutants/mutmut-stats.json`，在 `mutants/` 里跑找不到。
- **`tests/unit/test_skill_script_path_guards.py` 被整体排除**，理由在 `[tool.mutmut]` 注释。排除只会多出假存活（多复核一个），不会造成假杀死。
- **`timeout_multiplier` 不要调小。** 调小不省时间，只会让已证实的真存活被记成 killed，把无效测试藏起来。
- **−11（段错误）成批出现即整轮作废，不要逐个复核。** 已知的一种成因在 macOS：环境里没有 `*_proxy` 变量时，`urllib.request.getproxies()` 经 `_scproxy` 调 SystemConfiguration 读系统代理，该框架在 fork 出的多线程子进程里直接段错误，凡构造 `openai.OpenAI` / `httpx.Client` 的用例都中招（第二批 2186 个里 577 个），而同一个 mutant 在新进程里复核却正常。`tests/mutmut_plugin.py` 已把这两个入口换成常量实现；再成批出现就用 `PYTHONFAULTHANDLER=1 uv run mutmut run <mutant>` 单跑一个看崩溃栈，别当成负载或偶发。判据：−11 只该零星出现（首批 616 个里 2 个）。
- **测试选集走全量。** 只跑 `tests/unit` 快 5.5 倍，但约 45% 的存活是假的，每个都要复核一次全量套件，总账更贵。

## 7. 成本参考

首批 8 模块、共 616 个 mutant 的实测：

| 项 | 数值 |
| --- | ---: |
| mutant 密度 | 约 1100 个 / 千行源码 |
| 一轮墙钟（8 路并行） | 8 模块 616 个 mutant 约 10 分钟（不含全量 stats 约 5 分钟）；超时归零前是 17:47，墙钟随超时数走 |
| 超时新进程复核 | 3 到 11 秒 / 个 |
| 逐条判定 | 约 3 秒 / 个 |
