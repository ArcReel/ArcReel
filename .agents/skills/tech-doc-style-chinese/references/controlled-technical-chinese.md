# Controlled Chinese technical writing

This module draws on ASD-STE100's controlled-language approach to reduce ambiguity and execution risks in Chinese technical instructions. It is not a Chinese version of ASD-STE100 and does not imply that output complies with or is certified under ASD-STE100.

ASD-STE100 is a standard for English technical documentation. English controlled-vocabulary, part-of-speech, tense, and sentence-length rules cannot be directly transferred to Chinese. This module adopts only transferable methods, restated for Chinese syntax and technical-writing contexts.

Official resources:

- [Official ASD-STE100 introduction](https://www.asd-ste100.org/about_STE.html)
- [ASD-STE100 Issue 9 download page](https://www.asd-ste100.org/STE_downloads.html)
- [ASD-STE100 tools and compliance information](https://www.asd-ste100.org/STEsoftware.html)

## Application intensity

### Apply fully

- Operating manuals
- Installation and deployment steps
- Troubleshooting
- Operations runbooks
- Safety warnings
- Multistep API call workflows

### Apply selectively

- API conceptual descriptions
- FAQs
- Changelogs
- Product capability descriptions

Selectively apply terminology consistency, explicit references, and factual fidelity; do not force every sentence into an operating step.

### Do not apply mechanically

- Brand and landing-page copy
- Narrative articles
- Original legal text and formal quotations
- Content requiring preservation of author style
- Very short button and navigation labels

## Core principles

### 1. Preserve facts

- Do not add numbers, dates, time limits, capabilities, conditions, or conclusions absent from the source.
- Do not remove warnings, constraints, exceptions, units, defaults, or failure handling.
- Do not change the degree of certainty expressed by words such as possible, suggested, planned, or usually.

### 2. Use one preferred term per concept

- Choose preferred terms for key objects, states, and actions.
- Do not rotate synonyms to avoid repetition.
- Expand abbreviations on first use and keep the same form afterward.
- Source project-specific nouns and verbs from the target project glossary or reliable context.
- When a word has several meanings, use a more specific term to disambiguate rather than replace mechanically.

### 3. Give each step one main action

- Start steps with explicit actions.
- Specify the action's object rather than only “处理”, “操作”, or “执行”.
- Split actions requiring sequential execution into numbered steps.
- Keep two actions in one step when they must occur simultaneously or form an indivisible operation.
- Do not split necessary conditions, negation, or causality merely to shorten sentences.

### 4. Put conditions and risks before actions

- Place conditions readers must know before execution ahead of the action.
- Place warnings before steps that could cause risk.
- List prerequisites separately when conditions are numerous.
- Do not bury key constraints in parentheses at the end of a step.

### 5. Make actors, objects, and results explicit

- Name the actor when responsibility or references could be ambiguous.
- Clearly distinguish automatic system behavior from manual actions.
- State expected results when needed to judge whether a step succeeded.
- Avoid “该”, “其”, “此”, “上述”, or “相关” when their objects cannot be identified.

### 6. Preserve logical order

- Describe operations in actual execution order.
- Start troubleshooting with low-risk, reversible checks with high evidentiary value.
- Separate causes, symptoms, operations, and results rather than mix them in one long sentence.
- Make negation scope explicit so readers know which action is forbidden.

## Operating-step template

Where applicable, organize as follows:

1. Prerequisites
2. Risks or constraints
3. Numbered operating steps
4. Expected result of each step
5. Stop conditions and recovery methods on failure

Do not invent expected results or recovery steps absent from the original. Mark missing information “pending confirmation”.

## Troubleshooting template

1. Describe observable symptoms.
2. State the logs, statuses, or error codes to collect.
3. Order checks by evidence and risk.
4. State decision criteria for each check.
5. Give causes only when evidence supports them.
6. Specify recovery actions, verification methods, and stop conditions.

Do not rewrite a “possible cause” as a “definite cause”.

## Regression examples

Use these examples to check factual fidelity, condition order, action boundaries, and oversimplification risks. Rewrites must not add information absent from the original example.

### Operating manuals

#### Example 1

Before:

`在安装代理程序之前需要确认服务器能够访问更新地址，然后运行安装命令。`

After:

1. `确认服务器能够访问更新地址。`
2. `运行安装命令。`

Check: conditions precede actions; no network address or command was added.

#### Example 2

Before:

`选择保存以后系统会写入配置并重新加载服务。`

After:

`选择「保存」。系统随后写入配置并重新加载服务。`

Check: manual actions and system behavior are distinct; system behavior is not misrepresented as manual steps.

#### Example 3

Before:

`如果设备温度超过允许范围，不要继续启动设备。`

After:

`如果设备温度超过允许范围，不要启动设备。`

Check: retain the condition and prohibition without adding unknown temperature values.

### API documentation

#### Example 4

Before:

`调用接口前应配置访问令牌，否则请求会返回认证错误。`

After:

`调用接口前，配置访问令牌。未配置访问令牌时，请求返回认证错误。`

Check: separate prerequisites and failure results without adding status codes.

#### Example 5

Before:

`请求成功后接口会返回任务 ID，后续可以使用这个 ID 查询处理状态。`

After:

`请求成功后，接口返回任务 ID。使用该任务 ID 查询处理状态。`

Check: keep object names consistent and avoid the unidentifiable “这个 ID”.

#### Example 6

Before:

`请求失败时最多重试 3 次，每次间隔 5 秒。`

After:

`请求失败时，最多重试 3 次。每次重试间隔 5 秒。`

Check: original retry counts and intervals remain unchanged.

### Troubleshooting

#### Example 7

Before:

`页面无法打开时可能是网络问题，可以先检查浏览器是否能够访问其他站点。`

After:

`页面无法打开时，先检查浏览器能否访问其他站点。网络异常是可能原因之一。`

Check: a possible cause was not turned into a definite cause.

#### Example 8

Before:

`查看服务日志并找到最早出现的错误，然后记录错误时间和请求 ID。`

After:

1. `查看服务日志，找到最早出现的错误。`
2. `记录错误时间和请求 ID。`

Check: split actions in execution order without adding log locations.

#### Example 9

Before:

`排查期间不要删除日志文件，因为后续分析还需要这些文件。`

After:

`排查期间，不要删除日志文件。后续分析仍然需要这些文件。`

Check: retain the prohibited action and its reason.

### Product introductions

#### Example 10

Before:

`平台提供任务创建、状态查询和结果下载能力。`

After:

`平台提供任务创建、状态查询和结果下载能力。`

Check: the original is already clear; do not rewrite merely for variety.

#### Example 11

Before:

`通过一站式能力赋能团队快速完成数据处理。`

After:

`平台集中提供数据处理功能，帮助团队快速完成数据处理。`

Check: remove vague “一站式能力” and “赋能”, retaining the original “快速” claim; do not add speed metrics yourself.

#### Example 12

Before:

`系统通常会在任务完成后发送通知。`

After:

`系统通常在任务完成后发送通知。`

Check: retain the uncertainty expressed by “通常”.

## Automated-check boundaries

Can be checked reliably by automation:

- Definite typos
- Unambiguous terminology capitalization
- Incorrect handling of machine-readable content
- Definite mappings in the project vocabulary

Should only prompt human judgment:

- Whether a sentence contains too many actions
- Whether conditions should move earlier
- Whether the subject needs explicit naming
- Whether pronouns are ambiguous
- Whether passive phrasing impairs understanding
- Whether text is oversimplified
- Whether rewriting changes facts or certainty

Do not make sentence length, passive voice, pronouns, or context-dependent terms unconditional failure rules. Automated tools provide hints only; they cannot prove ASD-STE100 compliance or replace author judgment.
