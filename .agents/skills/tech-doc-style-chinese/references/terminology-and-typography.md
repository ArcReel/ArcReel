# Terminology and typography

Read this file only when handling terminology, wrong words, punctuation, spacing, or capitalization. All rules apply only to visible body text, not code, paths, fields, commands, configuration options, or fixed literals specified by the user.

## Terminology principles

- Use one preferred term per concept.
- Prefer the target project glossary and official product spellings.
- Do not substitute synonyms merely for variety.
- When words have multiple valid meanings, judge context first rather than replace globally.
- Put project-specific terminology in the target project's override file.

## Vague language and jargon

The following terms can obscure concrete actions. Use them only with explicit business definitions, as formal terminology, in original quotations, or when the user requests preservation:

- `赋能`
- `抓手`
- `闭环`
- `沉淀`
- `对齐`
- `对标`
- `拉通`
- `打通`
- `协同`
- `联动`
- `洞察`
- `赛道`
- `心智`
- `调性`
- `战役`
- `链路`
- `势能`
- `兜底`
- `落盘`
- `收口`
- `透传`

Prefer stating what they actually mean:

- `赋能` -> `提供`, or name the capability provided directly
- `抓手` -> `关键措施`
- `闭环` -> `完整流程`, or list start, processing, and completion conditions
- `沉淀` -> `形成`、`积累`、`保存`
- `对齐` -> `统一`、`确认一致`
- `拉通` / `打通` -> `连接`、`贯通`
- `洞察` -> `分析结论`
- `兜底` -> state the specific fallback mechanism after failure
- `落盘` -> `写入文件`、`保存到本地`
- `透传` -> `原样传给下游`

The following terms depend on context and must not be automatically replaced by the checker:

- `场景`
- `生态`
- `体系`
- `路径`
- `触点`
- `卡点`
- `布局`
- `矩阵`
- `颗粒度`
- `复盘`
- `梳理`
- `输出`
- `提炼`

When retaining internal terminology, explain its actual meaning on first use and avoid stacking multiple terms.

## Punctuation

- Use full-width Chinese punctuation in Chinese body text by default.
- Use corner brackets `「」` for Chinese quotations and `『』` for nested quotations; project, regional, publishing, or fixed-quotation conventions may override this.
- Do not add spaces around full-width Chinese punctuation; English sentences and code follow their own rules.
- Use full-width parentheses `（）` in Chinese descriptions, without interior spaces. Keep parentheses in original English text, code, and function calls unchanged.
- Use title brackets `《》` for documents, books, and similar works; use `「」` for button, menu, and field display names. Use inline code for machine-readable field names and preserve them unchanged.
- Use `……` for Chinese ellipses and `——` for Chinese dashes rather than three English periods or repeated hyphens. Keep code, command options, and Markdown structural markers unchanged.
- Avoid repeated exclamation marks, ellipses, and slogan-like fragments.
- Use periods consistently for full-sentence list items. Phrase items may omit periods, but keep each list consistent.
- Separate parallel terms with enumeration commas and parallel clauses with commas or semicolons according to hierarchy.
- Use colons only before actual explanations, enumerations, or quotations; do not split every sentence into a colon-led list.
- Use dashes, parentheses, and slashes only for clear relationships, not as substitutes for sentence structure.

The counterexamples below concern only editable Chinese body text; they do not require changing fixed quotations or original code:

| Not recommended | Recommended |
| --- | --- |
| `点击“保存”按钮。` | `点击「保存」按钮。` |
| `提示为「请选择「保存」」。` | `提示为「请选择『保存』」。` |
| `应用程序接口( API )` | `应用程序接口（API）` |
| `参见「安装指南」。` | `参见《安装指南》。` |
| `支持 API 、 SDK 和命令行 。` | `支持 API、SDK 和命令行。` |
| `正在加载...` | `正在加载……` |
| `检查结果--连接失败。` | `检查结果——连接失败。` |

## Address

- By default, technical documentation does not directly address readers; prefer subjectless sentences or explicit roles.
- Use `开发者`, `技术负责人`, `实施人员`, `业务负责人`, or `产品经理` according to context.
- `你`, `您`, and `同学` are style hints, not universal language errors; consumer products, help text, or brand guidelines may allow second person.

## Spacing between Chinese, English, and numbers

In visible body text, insert one ordinary half-width space (U+0020) between Chinese and English words, English abbreviations, Arabic numerals, or version numbers, known as “盘古之白”. This is not half a space; do not simulate alignment with full-width spaces, repeated spaces, or tabs. Exceptions are Markdown indentation, table structures, and two trailing spaces indicating explicit line breaks.

| Not recommended | Recommended |
| --- | --- |
| `使用API获取数据` | `使用 API 获取数据` |
| `等待3秒` | `等待 3 秒` |
| `版本2.0已发布` | `版本 2.0 已发布` |
| `增长50%` | `增长 50%` |
| `使用  API 获取数据` | `使用 API 获取数据` |

Insert one half-width space between Chinese and inline code, adjusting only outside code delimiters without modifying code contents. For example:

- Not recommended: ``运行`npm install`安装依赖。``
- Recommended: ``运行 `npm install` 安装依赖。``
- Do not add spaces between inline code and adjacent Chinese punctuation, for example: ``运行 `npm install`。``

Judge link boundaries by displayed text; do not insert typographic spaces inside link syntax or change targets. For example:

- Not recommended: `参见[API](https://example.com)说明。`
- Recommended: `参见 [API](https://example.com) 说明。`
- Do not add spaces around Chinese link text: `参见[安装说明](https://example.com)。`

These rules defer to target-project conventions. Do not mechanically replace text inside:

- Inline code and code blocks
- JSON keys
- URLs and API paths
- Database field names
- Commands, configuration options, placeholders, and regular expressions
- Table headings directly mirroring interface definitions
- Official product spellings and user-specified literals

## Numbers, units, and dates

- Insert one half-width space between a number and a half-width unit symbol, for example `10 GB`, `200 ms`, and `25 °C`.
- Keep percent and degree signs adjacent to numbers, for example `50%` and `90°`; the temperature unit `°C` differs from the degree sign `°`.
- Space Chinese from complete numeric values without splitting their internal structure, for example `增长 50%`, `旋转 90° 后停止`, and `温度为 25 °C`.
- Do not space minus signs, decimal points, or thousands separators from digits. Preserve existing values and precision; do not convert units or alter decimal places for typography.
- Prefer `至` for numeric ranges in ordinary Chinese, for example `3 至 5 天`; preserve bounds and inclusivity, leaving code connectors unchanged.
- Write Chinese dates as `2026 年 9 月 29 日` and Chinese times as `下午 3 点`; do not space inside numeric times, for example `15:30`.
- State known time zones for cross-time-zone audiences. If missing, request the information rather than guess from location. English dates follow target-region conventions; do not automatically convert ambiguous all-numeric dates.

| Not recommended | Recommended |
| --- | --- |
| `容量为10GB` | `容量为 10 GB` |
| `耗时200ms` | `耗时 200 ms` |
| `增长 50 %` | `增长 50%` |
| `旋转 90 °` | `旋转 90°` |
| `温度为25°C` | `温度为 25 °C` |
| `偏移为 - 3.5` | `偏移为 -3.5` |
| `2026年9月29日` | `2026 年 9 月 29 日` |
| `15 : 30` | `15:30` |

Also check numeric typography for:

- Complete dates, times, and time zones
- Confusion between percentages and percentage points
- Necessary units on values
- Explicit range bounds and inclusivity
- Consistent currency, temperature, storage units, and thousands separators

## Paragraph wrapping and source line width

Keep Chinese Markdown paragraphs on one source line per paragraph. Do not manually break paragraphs to control source line width; let the editor soft-wrap long lines.

Reasons to remove hard wraps:

- Manual paragraph wrapping fragments source files and makes context harder to follow during rewriting, comparison, and review.
- Most renderers treat soft line breaks as spaces; breaks at Chinese/English boundaries may add or swallow spaces.

Keep one paragraph per line for:

- Body paragraphs
- List-item body text; preserve list hierarchy, markers, and indentation

Preserve existing line breaks in:

- Fenced and indented code blocks, including fences after list markers and indented code within list items
- Table row structures
- Headings, Setext underlines, separators, and link-reference definitions
- Entire HTML blocks, including multiline `<pre>` and multiline comments
- Blockquotes, which may need to retain their original form
- Explicit line breaks indicated by two trailing spaces or a backslash

After unwrapping, recheck spacing at every join:

- Do not insert spaces between Chinese characters
- Insert one space between Chinese and half-width English or numbers, for example `作为 ASR` and `的 stdio 模式`
- Do not space around full-width punctuation, for example `说明，API 返回`
- Judge inline code, links, and emphasis by visible text, for example `详见 **接口说明**文档`

Use `scripts/unwrap_md_paragraphs.py` for bulk processing. It handles only joins without modifying existing interior spacing, punctuation, or wording; results still need manual review.

## Technical-term capitalization

These common recommended spellings must not override official project or product naming:

- `id` / `Id` -> `ID`
- `http` / `Http` -> `HTTP`
- `url` / `Url` -> `URL`
- `json` / `Json` -> `JSON`
- `api` / `Api` -> `API`
- `ai` / `Ai` -> `AI`
- `javascript` -> `JavaScript`
- `typescript` -> `TypeScript`
- `llm` / `Llm` -> `LLM`
- `aigc` / `Aigc` -> `AIGC`
- `rag` / `Rag` -> `RAG`
- `chatgpt` / `Chatgpt` -> `ChatGPT`
- `openai api` / `OpenAI api` -> `OpenAI API`
- `python` -> `Python`
- `nodejs` -> `Node.js`
- `github` -> `GitHub`
- `gitlab` -> `GitLab`
- `postgresql` -> `PostgreSQL`
- `grpc` -> `gRPC`
- `graphql` -> `GraphQL`
- `websocket` -> `WebSocket`
- `yaml` -> `YAML`
- `xml` -> `XML`
- `jwt` -> `JWT`

The following forms depend on context and should only prompt manual confirmation:

- `JS`：may be appropriate in compact UIs or existing names; formal body text may use `JavaScript`
- `H5`：may mean mobile Web campaign pages, not the HTML5 standard
- `Postgres`：may be the project's official abbreviation
- `OAuth`：may refer to a protocol family; do not automatically expand to `OAuth 2.0`
- `k8s`：formal documentation may use `Kubernetes`，may remain in commands, labels, and community contexts

## Common wrong words and contextual terms

May be treated as definite errors:

- `阀值` -> `阈值`
- `布署` -> `部署`
- `反回` -> `返回`
- `回朔` -> `回溯`
- `做为` -> `作为`
- `embeding` -> `embedding`
- `提示工程学` -> `提示工程`

Must be judged in context:

- `登录系统`, but valid wording includes `登陆月球`
- `配置参数`, but valid wording includes `配制溶液`
- `截至 4 月 12 日`, but valid wording includes `截止日期`
- `启用功能`, but valid wording includes `起用新人`
- `标识字段`, but valid wording includes `在图中标示位置`
- Technical capabilities usually use `实时通信`, but `即时` may be correct in other contexts
- `认证` means verifying identity; `授权` means granting permissions

## Quantities and logic

- `缩小了 3 倍` -> `缩小到原来的 1/3` or specify the reduction ratio
- `从 20% 增长到 30%`: an increase of 10 percentage points, or 50% relative growth
- `转化率提升了 5%`: confirm whether the increase is 5% or 5 percentage points
- `翻了 1 倍` -> `变为原来的 2 倍`
- `不超过 100 以上` -> `不超过 100`
- `预计大约在 3 点左右` -> `预计 3 点左右`

When correcting expression, do not add bases, years, deadlines, or measurement results absent from the original.
