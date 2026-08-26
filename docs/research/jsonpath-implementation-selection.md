# JSONPath 实现选型与跨语言一致性调研

> 状态：调研完成，待 #2123（定义格式与模板语言细则）采纳。
> 关联：[#2122](https://github.com/ArcReel/ArcReel/issues/2122)（本票）、[#2119](https://github.com/ArcReel/ArcReel/issues/2119)（Wayfinder：自定义调用端点）、[#2123](https://github.com/ArcReel/ArcReel/issues/2123)（被本票阻塞）
> 调研日期：2026-08-26。版本号、CTS 提交号、实测结果均以当日为准。

## 0. 结论摘要

**推荐组合**

| 侧 | 推荐 | 理由 |
|---|---|---|
| 后端（Python） | `jsonpath-rfc9535`（jg-rp，PyPI 同名，import 名 `jsonpath_rfc9535`） | 严格 RFC 9535，只依赖 `regex` + `iregexp-check` 两个纯 Python 小包，本地跑 CTS 703/703 通过；与前端推荐库同作者、同架构，94 条实测路径逐条结果一致 |
| 前端（TypeScript） | `json-p3`（jg-rp） | RFC 9535 + JSON Pointer + JSON Patch，零依赖，自带类型，CTS 703/703 通过；min+gzip 约 15.7 KB；暴露与 Python 侧同名的 AST（`NameSelector` / `IndexSelector` / `SliceSelector` / `WildcardSelector` / `FilterSelector`），可做同构的语法子集校验 |

**不采用**：`jsonpath-ng`（CTS 34%，过滤器需 `ext` 解析器且不支持 `&&` / `!` / 函数，`.*` 对数组无效，越界与非法组合抛运行时异常）、`jsonpath2`（CTS 40%，ANTLR 运行时依赖，四年未发版，`$[2:113667776004]` 直接卡死）、`jsonpath-plus`（CTS 15%，过滤器是 JS 表达式而非 RFC 语法，不支持负下标 `[-1]`，畸形路径 `$['id'` / `$.id.` / `$.` 也能"成功"，两次 RCE CVE 且 README 自述不再积极维护）、`@astronautlabs/jsonpath` / `jsonpath`（dchester，CTS 32%，脚本表达式经 `static-eval`，2026 年又一次代码注入 CVE，不支持负下标与 RFC 过滤器）、`jsonpath-rfc9535`（npm，P0lip；CTS 94%，`..` 递归产出顺序与其它实现不同，且不做函数扩展的良构检查）。

**建议允许的语法子集**（细则见第 8 节）：

- 必须以 `$` 开头；只允许 child segment：点号简写 `.name`、括号名字 `['name']` / `["name"]`、非负与负整数下标 `[0]` / `[-1]`、通配 `[*]` / `.*`、切片 `[start:end]`（`step` 固定为 1，省略）。
- 过滤器只允许 `[?<expr>]` 形式（RFC 语法，不带外层括号），`<expr>` 只允许：`@` 起头的**单值查询**（仅名字/下标段）、存在性测试、与字面量（字符串 / 数字 / `true` / `false` / `null`）的 `==` `!=` `<` `<=` `>` `>=` 比较、`&&` `||` `!`、圆括号分组。
- **禁用**：`..` 递归下降、多选择器联合 `[0,1]` / `['a','b']`、切片 `step`、过滤器内引用 `$` 根、过滤器内的非单值查询（`@.*`、`@..x`、`@[?..]`）、函数扩展 `length()` `count()` `match()` `search()` `value()`、一切非 RFC 扩展（`?()` 内 JS 表达式、`(@.length-1)` 脚本、`=~` 正则、`and` / `or`、`^` / `~` / `#` / `@property` / `.length`）。
- 取值语义：优先级数组中每条路径求值得到节点列表，取**第一个节点**（数组按下标序；对象成员按 JSON 文本出现序，但见 6.1 的 JS 整数键重排问题——因此进一步建议**对象通配 `*` 的结果不得依赖顺序**，UI 校验时若路径含对象通配或结果数 > 1 给出提示）。

## 1. 问题

#2119 已定「响应提取用 JSONPath（RFC 9535）优先级数组」。本票回答三件事：

1. Python 与前端各候选实现对 RFC 9535 的符合度与差异点。
2. 同一路径在前端校验（试跑器离线校验、编辑时即时校验）与后端执行结果是否一致。
3. 哪些特性应禁用以收窄表达面、便于 UI 校验。

现状：`lib/video_backends/base.py` 的 `_dig` / `first_str_by_paths` 用元组路径 `("data", "task_result", "videos", 0, "url")` 逐层取值，任一层缺失返回 `None`；`docs/research/arcreel-video-api-protocol-research.md` 第 4 章的对照表已经以 `$.data.task_result.videos[0].url` 这类 JSONPath 记法描述各供应商的 task_id / 视频 URL 路径。前端当前无 JSONPath 依赖。

## 2. RFC 9535 要点（与本议题相关的部分）

来源：[RFC 9535](https://www.rfc-editor.org/rfc/rfc9535.txt)（2024-02，Proposed Standard）。

- **文法**（§2.1.1、§2.3、§2.5）：`jsonpath-query = root-identifier segments`；segment 分 `child-segment`（`[...]` 或 `.name` / `.*`）与 `descendant-segment`（`..name` / `..*` / `..[...]`）；selector 五种：`name-selector`（字符串字面量，单双引号均可）、`wildcard-selector`、`index-selector`（整数，不允许前导 0、不允许 `-0`）、`slice-selector`（`start:end:step`，Python 语义，`step` 可负）、`filter-selector`（`?` + 逻辑表达式）。点号简写 `member-name-shorthand` 的首字符只能是 `ALPHA / "_" / 非 ASCII`，后续字符再加 `DIGIT`；**不含 `-`、`$`、空格**，这些键必须用括号记法。
- **下标**（§2.3.3.2）：负下标从末尾倒数；越界"不选中任何节点，且不是错误"。
- **通配**（§2.3.2.2）：数组子节点按数组序；**对象子节点顺序不作规定**。
- **递归下降**（§2.5.2.2）：节点先于其后代访问；数组按序；对象成员访问顺序不作规定。
- **过滤器**（§2.3.5）：比较运算 `==` `!=` `<` `<=` `>` `>=`，逻辑 `&&` `||` `!`；比较两侧只能是字面量、**单值查询**（只含名字/下标段的查询）或返回 `ValueType` 的函数；非单值查询直接放在比较里是**非良构**，实现 MUST 报错。单独出现的查询是存在性测试。`==` 对对象/数组做深比较；`<` 只在两侧同为数字或同为字符串时可能为真，类型不同一律 false。字符串按 Unicode 标量值比较。
- **函数扩展**（§2.4）：`length()` `count()` `match()` `search()` `value()`，带类型系统（`ValueType` / `LogicalType` / `NodesType`），参数类型与返回位置错误属于非良构；正则语法是 I-Regexp（RFC 9485）。
- **良构与有效**（§2.1）：实现 MUST 对非良构或无效查询报错——这正是 UI 校验能否与后端一致的前提：宽松解析器"不报错"本身就是不符合。
- **节点列表**（§2.1.2）：重复节点不去重。
- **规范化路径**（§2.7）：`$['a'][0]` 形式，可作为试跑器展示命中位置的统一格式。
- **安全考虑**（§4）：`match()` / `search()` 携带用户正则有 ReDoS 面；深层嵌套/超大数组的资源耗尽。

## 3. 候选库概况

数据来源：PyPI / npm registry JSON、GitHub API（stars / 最近 push / open issues）、GitHub Advisory Database、各库 README 与源码；采集日 2026-08-26。体积为本地安装包实测（`node_modules` 内 dist 文件 `gzip` 后大小）。

### 3.1 Python

| 库 | 最新版（发布日） | 运行时依赖 | wheel | 许可证 | 仓库活跃度 | RFC 9535 立场 |
|---|---|---|---|---|---|---|
| `jsonpath-rfc9535`（jg-rp） | 1.0.0（2025-11-30） | `iregexp-check`、`regex`（I-Regexp 校验与匹配） | 36 KB | MIT | 10 star，最近 push 2025-11-30，open issues 0 | 严格实现，跑 CTS；本地 703/703 |
| `python-jsonpath`（jg-rp） | 2.2.1（2026-07-07） | 无；`[strict]` extra 加 `iregexp-check`、`regex` | 65 KB | MIT | 78 star，最近 push 2026-07-28，open issues 3 | 超集：默认宽松（无 `$`、`and`/`or`、`=~`、`~`/`#`/`^`、`in`/`contains`、`\|`/`&`），`strict=True` 时为 RFC 行为；文档明示"需要严格符合时传 `strict=True`"；本地 686/703（差异全为刻意扩展） |
| `jsonpath-ng` | 1.8.0（2026-02-28） | 无（`ply` 以 `_ply` 内嵌） | 68 KB | Apache-2.0 | 734 star，最近 push 2026-08-01，open issues 87 | 不声称 RFC；`jsonpath-rw` 后继，过滤器只在 `jsonpath_ng.ext.parse` 且语法自成一派；本地 239/703（`ext` 263/703） |
| `jsonpath2` | 0.4.5（2022-05-07） | `antlr4-python3-runtime==4.10` | 34 KB | LGPL-3.0 | 53 star，最近 push 2022-12-14 | 不声称 RFC；四年无发布；本地 282/703，大整数切片卡死 |

### 3.2 JavaScript / TypeScript

| 库 | 最新版（发布日） | 运行时依赖 | 体积（min+gzip） | 类型 | 许可证 | 仓库活跃度 | RFC 9535 立场 / 安全 |
|---|---|---|---|---|---|---|---|
| `json-p3`（jg-rp） | 2.2.2（2025-03-18） | 无 | 约 15.7 KB（`iife.min`，含 JSON Pointer/Patch）；ESM 未压缩 155 KB / 30 KB gz | 自带 `dist/index.d.ts`（TS 源码） | MIT | 31 star，最近 push 2025-10-16（更新 CTS），open issues 0，无 GHSA | 跑 CTS（`tests/path/compliance.test.ts`），本地 703/703；`strict` 默认 `true`，非标准扩展（`~`、`#`、`~?`）仅在 `strict:false` 下启用 |
| `jsonpath-rfc9535`（P0lip） | 1.3.0（2025-04-04） | 无 | ESM 目录 44 文件共 125 KB 未压缩；无官方 min 包 | 自带 | Apache-2.0 | 12 star，最近 push 2026-06-04（解析修复未发版），open issues 2 | README 声称对 CTS 旧 commit 100%；本地对当前 CTS 662/703；不支持自定义函数扩展；`..` 顺序与 jg-rp 系不同 |
| `jsonpath-plus` | 10.4.0（2026-02-16） | `jsep`、`@jsep-plugin/regex`、`@jsep-plugin/assignment` | 约 8 KB（`index-browser-esm.min`） | 手写 `src/jsonpath.d.ts`（多 `any`） | MIT | 1157 star，最近 push 2026-08-20，open issues 50；README 注明"not currently being actively maintained"；2026-07/08 两次 safe-eval 安全修复尚未发到 npm | 不声称 RFC（仅"兼容原始 jsonpath spec"），不跑 CTS；过滤器/脚本是 JS 表达式（`jsep` + 自研 SafeEval）；CVE-2024-21534（Critical，RCE，<10.2.0）、CVE-2025-1302（High，RCE，<10.3.0） |
| `jsonpath`（dchester） | 1.3.0（2026-03-05） | `esprima`、`static-eval`、`underscore` | 约 27.7 KB（`jsonpath.min.js`） | 无 | MIT | 1430 star，open issues 106；2026 年提交全部是安全修复 | 不声称 RFC；CVE-2025-61140（原型污染，<1.2.0）、CVE-2026-1615（High，`static-eval` 代码注入，≤1.2.1） |
| `@astronautlabs/jsonpath` | 1.1.2（2023-06-19） | `static-eval` 2.0.2（`esprima` 内嵌） | ESM 目录 32 文件共 352 KB 未压缩 | `dist/index.d.ts`（几乎全 `any`） | MIT | 12 star，最后实质提交 2021-01 | dchester fork，语义相同；与 CVE-2026-1615 同源的 `static-eval` 路径是否受影响未验证 |

另有 `@swaggerexpert/jsonpath`（4.0.4，依赖 `apg-lite`，README 声称 CTS 100%）与 `jsonpathly`（3.0.0，自称 RFC 9535 但未声明跑 CTS，带 `=~`/`in` 扩展）两个 RFC 系实现，未纳入实测。

## 4. 符合度实测：JSONPath Compliance Test Suite

方法：下载 [jsonpath-standard/jsonpath-compliance-test-suite](https://github.com/jsonpath-standard/jsonpath-compliance-test-suite) 的 `cts.json`（main 分支 commit `7be7c1f`，2026-05-21），共 703 条用例（456 条合法查询比对结果、247 条非法查询要求实现报错、其中 9 条允许多种结果顺序）。逐库在本机运行，"有效通过"= 结果与期望逐项相等（含顺序，允许 `results` 列出的任一顺序），"非法拒绝"= 抛出异常。脚本与原始输出保存在会话 scratchpad（`cts_py3.py` / `cts_js.mjs`），未入库。

| 库 | 有效查询通过 | 非法查询拒绝 | 合计 |
|---|---|---|---|
| `jsonpath-rfc9535` 1.0.0（Python） | 456/456 | 247/247 | **703/703（100%）** |
| `json-p3` 2.2.2（TS） | 456/456 | 247/247 | **703/703（100%）** |
| `python-jsonpath` 2.2.1（默认非严格模式） | 455/456 | 231/247 | 686/703（97.6%） |
| `jsonpath-rfc9535` 1.3.0（npm，P0lip） | 453/456 | 209/247 | 662/703（94.2%） |
| `jsonpath2` 0.4.5 | 86/456 | 196/247 | 282/703（40.1%）\* |
| `jsonpath-ng` 1.8.0（`jsonpath_ng.ext.parse`） | 175/456 | 88/247 | 263/703（37.4%） |
| `jsonpath-ng` 1.8.0（`jsonpath_ng.parse`） | 115/456 | 124/247 | 239/703（34.0%） |
| `@astronautlabs/jsonpath` 1.1.2 / `jsonpath` 1.3.0（dchester） | 86/456 | 137/247 | 223/703（31.7%） |
| `jsonpath-plus` 10.4.0（默认 `eval: 'safe'`） | 101/456 | 6/247 | 107/703（15.2%） |
| `jsonpath-plus` 10.4.0（`eval: false`） | 101/456 | 36/247 | 137/703（19.5%） |

\* `jsonpath2` 遇到 `$[2:113667776004]` 一类大整数切片会把 Python `range` 逐项物化，进程长时间无响应；12 条此类用例被跳过并计为失败。

失败分布（摘要）：

- `python-jsonpath` 非严格模式的 16 条"接受了非法查询"全部是刻意的宽松：允许首尾空白、`True`/`Null` 大写、`00`/`01` 数字、`$[$.a]` 单值查询选择器、孤立代理对转义；1 条结果错误是 `$[?@[0] == 5]`（把整数下标当成对象键 `"0"` 匹配）。这 17 条与仓库 `tests/test_compliance.py` 里的 `XFAIL_INVALID` / `XFAIL_VALID`（仅在 lax 模式下预期失败）逐条对应；该测试在 `JSONPathEnvironment(strict=True)` 下跑完整 CTS（submodule 同为 `7be7c1f`）。传 `strict=True` 即得到与 `jsonpath-rfc9535` 相同的行为（后者是同作者抽出的严格子集包，README："We follow RFC 9535 strictly and test against the JSONPath Compliance Test Suite"，`test_compliance.py` 的 `SKIP` 为空）。
- npm `jsonpath-rfc9535` 的 3 条结果错误都是 `&&` 三个以上连写（`$[?@.a && @.b && @.c]`）；38 条未拒绝是超出 I-JSON 精确整数范围的下标/切片，以及函数扩展的良构检查（参数个数、`count(1)`、`length(@.*)`、`match()` 结果直接参与比较等）。README 声称对某一旧 commit 100% 通过，当前 CTS 已扩充。
- `jsonpath-plus`：156 条 whitespace 用例、110 条 filter 用例、42 条 functions 用例失败；本质是它不实现 RFC 过滤器文法（`[?@.a=='b']` 返回空），过滤器必须写成 `[?(...)]` 并按 JS 表达式求值。非法查询几乎全部"成功返回"。
- `jsonpath-ng`：301 条 `JsonPathLexerError`（`?`、`&&`、`!`、`☺` 等直接 lex 失败）；`$[-3]`、`$[0]`（作用于对象时）、`$..[1]` 抛 `IndexError` / `KeyError` 而非返回空。仓库自身也承认：issue #223（2026-03，open）"not RFC 9535 compliant: filters and filter functions like `match`"，issue #8 "Filters not implemented"（2017 起 open，基础解析器无过滤器），issue #11 `*` 不匹配数组元素（源码 `Fields.reified_fields` 对 list 返回空），issue #203 负下标越界抛 `IndexError`。1.8.0 因 PLY 归档与 CVE-2025-56005 改为内嵌 `_ply`，同时引入回归 #216（`ext.find()` 丢嵌套 dict 键）；issue #233 指出每次 `parse()` 都重建解析表（约 2.5 ms）。

## 5. 同路径跨库实测矩阵

方法：构造一份模拟供应商回包（含 `data.task_result.videos[]`、`output[]`、`items[]`（含 `kind` 字段）、带空格/中文/连字符/`$` 的键、数字字符串键、`null`、空数组），对 94 条路径在 11 个（库 × 模式）组合上求值，记录值列表或异常类型。完整矩阵在 scratchpad `matrix.txt`。下表按特性归纳差异（✅ 与 RFC 一致；空 = 返回空列表；❌ 抛异常；⚠ 结果与 RFC 不同）。

### 5.1 列表下标与负下标

| 路径 | rfc9535(py) / json-p3 | python-jsonpath | jsonpath-ng | jsonpath2 | jsonpath-plus | astronautlabs / dchester | rfc9535(js) |
|---|---|---|---|---|---|---|---|
| `$.data.task_result.videos[0].url` | ✅ `u0` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `…videos[-1].url` | ✅ `u1` | ✅ | ✅ | ✅ | **⚠ 空** | **⚠ 空** | ✅ |
| `$.output[5]`（越界） | 空 | 空 | 空 | 空 | 空 | 空 | 空 |
| `$.output[-0]` / `$.output[01]` | ❌ 语法错（RFC 规定） | ❌ | ⚠ 接受 | 接受 / ❌ | ⚠ 空 | ⚠ 接受 / ❌ | ❌ |
| `$[0]`（根是对象） | 空 | 空 | **❌ KeyError** | 空 | 空 | 空 | 空 |
| `$.obj[1]`（对象有键 `"1"`） | 空 | **⚠ `"one"`** | ❌ KeyError | 空 | **⚠ `"one"`** | 空 | 空 |
| `…videos.0.url`（点号数字） | ❌ | ❌ | ⚠ 空 | ❌ | ⚠ `u0` | ⚠ `u0` | ❌ |

`jsonpath-plus` 与 dchester 系对 `[-1]` 返回空而非报错，是前后端不一致里**最危险**的一类：前端校验通过、试跑显示"没取到"，后端却取到了末元素。

### 5.2 切片

| 路径 | rfc9535(py) / json-p3 | python-jsonpath | jsonpath-ng | jsonpath2 | jsonpath-plus | astronautlabs | rfc9535(js) |
|---|---|---|---|---|---|---|---|
| `[0:1]`、`[:1]`、`[-2:]` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `[::-1]` | ✅ 逆序 | ✅ | ✅ | ⚠ 空 | ⚠ 空 | ✅ | ✅ |
| `$.output[1:0:-1]` | ✅ `o1` | ✅ | ✅ | ✅ | ⚠ 空 | ✅ | ✅ |

### 5.3 通配 `*`

| 路径 | rfc9535(py) / json-p3 | python-jsonpath | jsonpath-ng | 其它 |
|---|---|---|---|---|
| `…videos[*].url` | ✅ | ✅ | ✅ | 全部 ✅ |
| `…videos.*.url` | ✅ | ✅ | **⚠ 空** | 其余 ✅ |
| `$.obj[*]`（对象） | ✅ 成员值 | ✅ | **⚠ 返回对象本身** | 其余 ✅ |
| `$[*]`（根对象） | ✅ 成员值 | ✅ | ⚠ 返回根本身 | 其余 ✅ |

### 5.4 递归下降 `..`

| 路径 | rfc9535(py) / json-p3 | python-jsonpath | jsonpath-ng | jsonpath2 | jsonpath-plus | astronautlabs | rfc9535(js) |
|---|---|---|---|---|---|---|---|
| `$..url` | `u0,u1,v0,i1,v2,n` | 同 | 同 | 同 | 同 | 同 | **`n,v0,i1,v2,u0,u1`** |
| `$..videos[0].url`、`$.data..videos[*].url`、`$.items..url` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `$..[0]` | ✅ | ✅ | **❌ KeyError** | ✅ | ✅ | ✅ | ⚠ 顺序不同 |
| `$..`（孤立） | ❌ | ❌ | ❌ | ❌ | **⚠ 返回根** | ❌ | ❌ |

npm `jsonpath-rfc9535` 对对象成员采用不同的访问顺序（先浅后深），RFC 允许（对象顺序不作规定），但与 Python 侧不一致；`..` 一旦命中多个同名键，"取第一个"的语义就在两端分叉。这是禁用 `..` 的直接依据之一。

### 5.5 过滤器 `?()`

| 路径 | rfc9535(py) / json-p3 | python-jsonpath | jsonpath-ng.ext | jsonpath-plus | astronautlabs / dchester | rfc9535(js) |
|---|---|---|---|---|---|---|
| `$.items[?@.kind=='video'].url`（RFC 形式） | ✅ `v0,v2` | ✅ | ✅ | **⚠ 空** | ❌ | ✅ |
| `$.items[?(@.kind=='video')].url`（带括号，RFC 也允许） | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `$.items[?@.url].url`（存在性） | ✅ 3 项 | ✅ | ✅ | ⚠ 空 | ❌ | ✅ |
| `$.items[?@.dur > 6].url` | ✅ `v2` | ✅ | ✅ | ⚠ 空（`?(@.dur>6)` 才可） | ❌ | ✅ |
| `$.items[?@.dur > '6']`（数字比字符串） | 空（RFC：类型不同为 false） | 空 | **❌ TypeError** | `?(@.dur>"6")` 形式下 **⚠ `v2`**（JS 隐式转换） | ❌ | 空 |
| `$.items[?(@.dur=="5")]`（jsonpath-plus 形式） | — | — | — | **⚠ `v0`**（JS `==` 宽松相等） | — | — |
| `&&` / `!` / `\|\|` | ✅ | ✅ | **❌ ParserError / LexerError** | 仅 `?( )` 内 ✅ | 仅 `?( )` 内 ✅ | ✅ |
| `and`（非标准） | ❌ | ⚠ 接受 | ❌ | ❌ | ❌ | ❌ |
| `length()` `match()` `search()` `count()` `value()` | ✅ | ✅ | ❌ | ⚠ 空 | ❌ | ✅ |
| `=~ /re/`（非标准） | ❌ | ⚠ 接受 | ❌ | ❌ / 空 | ❌ / 空 | ❌ |
| `$.items[?@.kind=='video' && $.status=='succeeded']`（引用根） | ✅ | ✅ | ❌ | ⚠ 空 | ❌ | ✅ |
| `$.items[?(@.kind=='video')][0]` | 空（RFC：对每个命中节点再取 `[0]`，对象无下标） | 空 | ❌ KeyError | 空 | 空 | 空 |
| `$.items[?(@.kind=='video')].url[0]` | 空（字符串无下标） | 空 | **⚠ `"v","v"`**（对字符串取字符） | **⚠ `"v","v"`** | 空 | 空 |

最后两行是产品语义上的坑：写作者直觉的"过滤后取第一个"在 RFC 里**不是**这么表达的（`[0]` 作用于每个命中节点而不是结果列表）。这正是"优先级数组取第一个节点"应当由取值层负责、而不让路径承担的原因。

### 5.6 脚本表达式与非标准扩展

| 路径 | RFC 系（rfc9535(py) / json-p3 / rfc9535(js)） | jsonpath-plus | astronautlabs / dchester | jsonpath-ng |
|---|---|---|---|---|
| `$.items[(@.length-1)].url` | ❌ 语法错 | ⚠ `v2` | ⚠ `v2` | ❌ |
| `$.output.length` | 空 | ⚠ `3`（JS 属性） | ⚠ `3` | 空 |
| `$.items[?(@.kind=='video')].url.length` | 空 | ⚠ `2,2` | 空 | 空 |
| `$.$ref`、`$.weird key` | ❌ | ⚠ 接受 | ❌ | ❌ |
| `$.a-b`（`-` 不在 ABNF 简写字符集） | jg-rp 两个实现 ⚠ 接受（`RE_PROPERTY` 允许非首位 `-`）；rfc9535(js) ❌ | ⚠ 接受 | ❌ | ⚠ 接受 |
| `$.中文` | ✅（非 ASCII 属简写字符集） | ✅ | `parse` ✅ / `ext.parse` ❌ | ❌ |

### 5.7 畸形路径（决定 UI 校验能否依赖解析器）

| 路径 | RFC 系 | python-jsonpath | jsonpath-ng | jsonpath-plus | astronautlabs |
|---|---|---|---|---|---|
| `id`（无 `$`） | ❌ | ⚠ 接受 | ⚠ 接受 | ⚠ 接受 | ⚠ 接受 |
| `$.`、`$.id.`、`$['id'`（未闭合）、`$.items[?]` | ❌ | ❌ | ❌ | **⚠ 全部"成功"** | ❌ |
| `$['id','status']` | ✅ 两值 | ✅ | ✅ | **⚠ 空** | ✅ |

`jsonpath-plus` 无法作为校验器：它对畸形输入不报错，对合法联合返回空。

### 5.8 一致性统计

94 条路径逐条比较（值序列 + 异常与否）：

| 组合 | 完全一致 |
|---|---|
| `jsonpath-rfc9535`(py) vs `json-p3` | **94/94** |
| `python-jsonpath`（非严格） vs `json-p3` | 89/94（差异全是 python-jsonpath 的扩展：`id` 无根、`and`、`=~`、`$.obj[1]` 整数键） |
| `jsonpath-rfc9535`(py) vs npm `jsonpath-rfc9535` | 89/94（差异全是 `..` 顺序与 `$.a-b`） |
| `jsonpath-ng.ext` vs `json-p3` | 65/94 |
| `jsonpath-ng.ext` vs `jsonpath-plus` | 45/94 |
| `jsonpath-rfc9535`(py) vs `jsonpath-plus` | 51/94 |
| `jsonpath2` vs `json-p3` | 52/94 |

## 6. 即使双方都 100% 符合 RFC，仍会分叉的点

### 6.1 JS 对象的整数样键会被重排

RFC 对对象成员顺序不作规定，两端都"合规"，但运行时行为不同。JSON 文本 `{"outputs":{"12":…,"3":…,"9":…,"b":…,"a":…}}`：

| 路径 `$.outputs.*.f` | 结果顺序 |
|---|---|
| Python（`json.loads` 保持文本序）：`jsonpath-rfc9535` / `python-jsonpath` / `jsonpath-ng` | `twelve, three, nine, bee, ay` |
| JS（`JSON.parse` 把整数样键升序排在前面）：`json-p3` / npm `jsonpath-rfc9535` / `jsonpath-plus` | `three, nine, twelve, bee, ay` |

ComfyUI `/history` 的 `outputs` 正是以节点 id（整数字符串）作键。因此"对象通配 + 取第一个"在前端试跑器与后端执行会取到不同元素。这不是库的问题，任何 JS 实现都如此；对策只能是语义约束（见第 8 节）。

### 6.2 `..` 的对象访问顺序

同上，RFC 不规定；npm `jsonpath-rfc9535` 与 jg-rp 系已实测不同（5.4）。即使两端都选 jg-rp 系，6.1 的键重排也会传导进 `..` 的结果顺序。

### 6.3 字符串比较与正则

RFC 要求按 Unicode 标量值比较；JS 原生字符串比较按 UTF-16 码元，BMP 之外的字符（emoji 等）排序可能不同——对本议题（比较状态码、类型标记）影响极低，记录备查。`match()` / `search()` 要求 I-Regexp（RFC 9485），Python `re` 与 JS `RegExp` 各有超集，且 RFC §4 明示 ReDoS 面；这是禁用函数扩展的额外理由。

### 6.4 数字

`?@.dur == 5.0` 在两端都命中 `5`（Python `5 == 5.0`，JS 单一 number 类型）。超出 I-JSON 精确整数范围的下标两端都拒绝。

## 7. 推荐组合与取舍

### 7.1 后端：`jsonpath-rfc9535`（Python）

- 严格实现，CTS 100%；运行时依赖只有 `regex`（`match()` / `search()` 用）与 `iregexp-check`（I-Regexp 校验）；非求值实现（无 `eval`），`max_recursion_depth = 100`，`max_int_index = 2**53-1`；`compile()` 返回 `JSONPathQuery`，暴露 `segments` / `selectors`、`singular_query()`、`JSONPathNode.path()`（规范化路径），足以在 schema 校验阶段做白名单 AST 检查。`JSONPathEnvironment(nondeterministic=True)` 会打乱对象成员顺序，可用来在测试里主动暴露 6.1 类问题。
- 风险：单人维护、10 star、2026 年零提交（1.0.0 于 2025-11-30 标记 stable，open issues 0）。缓解：RFC 已定稿、CTS 全过、接口极小，即使停更也能长期锁版；作者同时维护 `python-jsonpath`（2026-07 仍在发版）。
- 与 `python-jsonpath`（同作者，import 名 `jsonpath`，与 PyPI 上另一个 `jsonpath` 包同名，见其 issue #128）的关系：后者是超集，含 JSON Pointer / JSON Patch 与一批非标准扩展，`strict=True` 时行为等价，且其 CTS 测试正是在 strict 模式下跑的。若 #2123 后续需要 JSON Pointer（例如 `$each` 铺字段的定位），可换 `python-jsonpath[strict]` + `strict=True` 而不改路径语义；否则用小包。
- 备选被否定的理由：`jsonpath-ng` 的过滤器与运行时异常行为（5.1、5.5）；`jsonpath2` 的 ANTLR 依赖与大切片卡死。

### 7.2 前端：`json-p3`

- CTS 100%（`tests/path/compliance.test.ts` 同时比对 `values()` 与 canonical `paths()`），零依赖，TypeScript 源码自带类型；`iife.min` 约 62 KB / 15.7 KB gzip（含 Pointer + Patch），ESM 未压缩 155 KB / 30 KB gzip，ESM + CJS + IIFE 三发。`jsonpath.compile()` 返回 `JSONPathQuery`，其 `segments[].selectors[]` 与 Python 侧同名，`singularQuery()` 可用；过滤器 AST（`LogicalExpression` / `InfixExpression` / `RelativeQuery` / `FunctionExtension` / 各字面量）可遍历。`JSONPathEnvironment` 选项：`strict`（默认 `true`）、`nondeterministic`、`maxIntIndex` / `minIntIndex`、`maxRecursionDepth`（默认 50）、`functionRegister`（Map，可删掉五个内置函数以在解析期就拒绝函数扩展）。节点 API：`value` / `location` / `getPath({form:"canonical"})` / `toPointer()`。
- 它同时提供 JSON Pointer（RFC 6901）与 JSON Patch（RFC 6902），与 `python-jsonpath` 对称。
- `strict` 选项默认 `true`，源码注释注明"当前设为 false 无效"（文档列出的非标准扩展 `~` / `#` / `~?` 在 `strict:false` 下启用）；`$.a-b` 的宽松与 Python 侧一致，由子集校验器统一拒绝即可。
- 风险：31 star，2025-10 之后无提交（2025 年 38 次提交，最后一次是更新 CTS），open issues 0，无 GHSA。同 7.1 的缓解逻辑。
- npm `jsonpath-rfc9535`（P0lip）体积更小（ESM 目录 44 个文件共约 125 KB 原始），但 `..` 顺序与函数良构检查与 Python 侧不一致；若采纳本文子集（禁用 `..` 与函数），它也能用，但失去"同作者双端同构 AST"的便利。`jsonpath-plus` 不考虑（见 5.5、5.7）。

### 7.3 两端一致性的保障方式

- **单一真源在后端**：试跑器的"离线校验"（粘贴真实响应验证取值路径）走后端 HTTP API 求值，前端只做**语法与子集校验 + 即时预览**；预览结果与后端不一致时以后端为准。
- **共享测试向量**：把第 5 节的路径 × 回包用例固化为一份 JSON（`tests/fixtures/jsonpath_vectors.json` 之类），前端 vitest 与后端 pytest 各跑一遍，断言与 RFC 期望一致——这是前后端库升级时的回归闸门。
- **CTS 作为依赖升级闸门**（可选）：在 CI 里对两端库跑 CTS 子集（只跑本文允许的特性），避免升级引入偏差。

## 8. 建议允许的语法子集

目标：表达面足以覆盖 `arcreel-video-api-protocol-research.md` 第 4 章 15 家供应商的 task_id / 视频 URL / 状态路径与 ComfyUI 输出，同时让 UI 能对每个 token 给出确定的校验结果，并保证前后端"取第一个节点"落在同一个节点上。

### 8.1 允许

```
query      = "$" *segment
segment    = "." shorthand            ; shorthand 按 RFC ABNF：首字符 ALPHA / "_" / 非 ASCII，其后可含 DIGIT；不含 "-"
           / "." "*"
           / "[" selector "]"         ; 单个 selector，不允许逗号联合
selector   = name                     ; 'x' 或 "x"，含 RFC 转义
           / int                      ; 允许负数；不允许 -0、前导 0
           / "*"
           / [int] ":" [int]          ; 切片，无 step
           / "?" logical-expr
logical-expr = or-expr
or-expr    = and-expr *("||" and-expr)
and-expr   = basic *("&&" basic)
basic      = ["!"] ( "(" logical-expr ")" / test / comparison )
test       = rel-singular             ; 存在性测试
comparison = (rel-singular / literal) op (rel-singular / literal)
rel-singular = "@" *( "." shorthand / "[" name "]" / "[" int "]" )   ; 只允许名字/下标段
literal    = string / number / "true" / "false" / "null"
op         = "==" / "!=" / "<" / "<=" / ">" / ">="
```

覆盖示例：`$.id`、`$.data.task_result.videos[0].url`、`$.output[-1]`、`$.creations[0].url`、`$.outputs.*.gifs[0].filename`（ComfyUI，见 8.3 的顺序约束）、`$.items[?@.kind=='video'].url`、`$.data.response.resultUrls[0]`。

### 8.2 禁用及理由

| 特性 | 理由 |
|---|---|
| `..` 递归下降 | 对象访问顺序 RFC 不定、两端实测不同（5.4、6.2）；结果集可能很大；供应商回包的目标字段位置是确定的，不需要搜索 |
| 联合 `[0,1]`、`['a','b']` | 与"优先级数组"重复（数组本身就是联合），且 `jsonpath-plus` 等实现对联合行为不一 |
| 切片 `step`、`[::-1]` | 取值场景用不到；`jsonpath2` / `jsonpath-plus` 实现有偏差 |
| 过滤器内引用 `$` | 引入跨节点耦合，UI 无法就地校验；`jsonpath-plus` 返回空 |
| 过滤器内非单值查询（`@.*`、`@..x`、`@[?…]`、`@['a','b']`） | RFC 规定非单值查询不能参与比较，但可做存在性测试；一刀切禁用可让校验器只需检查"`@` 后只有名字/下标段" |
| 函数扩展 `length()` `count()` `match()` `search()` `value()` | 正则的 I-Regexp 子集两端实现差异与 ReDoS 面（6.3）；npm `jsonpath-rfc9535` 不做良构检查；取值场景无需求 |
| `?(…)` 内 JS 表达式、`(@.length-1)` 脚本、`.length`、`@property` / `@parent` / `^` / `~` / `#`、`=~`、`and` / `or`、无 `$` 前缀、首尾空白 | 均为特定实现的扩展，RFC 系两端一律报错；不进 schema 即可 |
| 非 RFC 简写字符（`$.a-b`、`$.$ref`、`$.weird key`） | 强制括号记法 `$['a-b']`，两端行为确定；同时消除 jg-rp 对 `-` 的宽松 |

### 8.3 取值语义约束（写进 #2123 的定义格式）

1. 优先级数组逐条求值，**第一条产生非空节点列表的路径胜出**；取该列表的第一个节点的值。空数组、`null` 视为"未命中"由格式层决定（建议：`null` 命中但值为 null → 继续下一条）。
2. 节点列表长度 > 1 时不报错但试跑器显示"命中 N 个，取第一个"；若路径含**对象通配**（`.*` / `[*]` 作用于对象）且命中 > 1，试跑器显式警告"顺序在前端预览与后端可能不同"（6.1）。建议起步模板里的 ComfyUI 路径改用过滤器定位而非依赖顺序，例如 `$.outputs[?@.gifs].gifs[0].filename`（过滤器仍作用于对象成员，顺序问题相同，但当只有一个节点产出视频时命中数为 1，警告不触发）。
3. 校验分两层：**语法层**用各自库的 `compile()`（非良构直接报错，两端一致）；**子集层**遍历 AST 拒绝 8.2 的构造（两端类名对齐：`DescendantSegment` / `JSONPathDescendantSegment`、`SliceSelector.step`、`FunctionExtension`、`RootQuery`、selectors 长度 > 1 等）。子集校验器两端各实现一份，用 7.3 的共享向量互相对拍。
4. 试跑器展示命中位置用 RFC §2.7 规范化路径（两端库都提供）。

## 9. 未决问题

- `python-jsonpath` 还是 `jsonpath-rfc9535`：取决于 #2123 是否需要 JSON Pointer / Patch；本文默认小包。
- 前端是否需要在编辑器里做实时求值预览（需把 `json-p3` 进主包，min+gzip 约 16 KB）还是只做语法/子集校验（可用一个手写的子集解析器替代，零依赖）。若只校验，可考虑不引入 `json-p3` 而由后端 API 承担全部求值；本文建议引入，理由是即时预览是试跑器"离线校验"模式的核心体验。
- `arcreel-video-api-protocol-research.md` 第 7 章"声明式 vs plugin"的既有结论需按 #2119 的决定加修订注，本文不动它。

## 来源

- RFC 9535 原文：<https://www.rfc-editor.org/rfc/rfc9535.txt>
- JSONPath Compliance Test Suite：<https://github.com/jsonpath-standard/jsonpath-compliance-test-suite>（`cts.json`，commit `7be7c1f`，2026-05-21）
- python-jsonpath 语法与非标准扩展：<https://jg-rp.github.io/python-jsonpath/syntax/>
- json-p3 语法：<https://jg-rp.github.io/json-p3/guides/jsonpath-syntax>；非标准扩展与 `strict`：<https://jg-rp.github.io/json-p3/guides/jsonpath-extra>
- jsonpath-rfc9535（Python）README 与 `tests/test_compliance.py`：<https://github.com/jg-rp/python-jsonpath-rfc9535>；源码 `lex.py`（`RE_PROPERTY` 允许非首位 `-`）：本地 1.0.0 安装包
- python-jsonpath `tests/test_compliance.py`（strict 模式跑 CTS、XFAIL 清单）：<https://github.com/jg-rp/python-jsonpath/blob/main/tests/test_compliance.py>；issue #128（import 名冲突）：<https://github.com/jg-rp/python-jsonpath/issues/128>
- json-p3 `tests/path/compliance.test.ts`、`src/path/environment.ts`：<https://github.com/jg-rp/json-p3>
- jsonpath-ng issues #8 / #11 / #203 / #216 / #223 / #233 与 CHANGELOG：<https://github.com/h2non/jsonpath-ng>
- jsonpath-plus 安全通告：<https://github.com/advisories/GHSA-pppg-cpfq-h7wr>（CVE-2024-21534）、<https://github.com/advisories/GHSA-hw8r-x6gr-5gjp>（CVE-2025-1302）；README 维护声明与 `src/Safe-Script.js`：<https://github.com/JSONPath-Plus/JSONPath>
- jsonpath（dchester）安全通告：<https://github.com/advisories/GHSA-6c59-mwgh-r2x6>（CVE-2025-61140）、<https://github.com/advisories/GHSA-87r5-mp6g-5w5j>（CVE-2026-1615）
- PyPI / npm registry JSON（版本、日期、依赖、体积）：`https://pypi.org/pypi/<name>/json`、`https://registry.npmjs.org/<name>`；GitHub API `repos/<owner>/<repo>`（star、push 日期、open issues）
- json-p3 源码 `dist/json-p3.esm.js`（`strict` 注释）：本地 2.2.2 安装包
- npm `jsonpath-rfc9535` README（零依赖、CTS 声明）：本地 1.3.0 安装包
- 实测脚本与输出：会话 scratchpad `jp/`（`cases.json`、`run_py.py`、`run_js.mjs`、`matrix.txt`、`cts_py3.py`、`cts_js.mjs`、各 `cts_*_out*.json`），未入库
