---
paths:
  - "lib/backends/**"
  - "lib/custom_provider/**"
  - "lib/config/**"
  - "lib/billing/**"
  - "lib/agent/agent_provider_catalog.py"
  - "lib/prompts/prompt_builders*.py"
  - "agent_runtime_profile/**"
  - "docs/api-docs/**"
---

# 供应商能力与契约

## 能力数据

### 每个能力维度只认一个来源，不在别处另写一份能力数值

按 [ADR 0104](../adr/0104-capability-sources-split-by-dimension.md) 判断能力来源。模型接受什么由模型目录条目持有，调用端点能实现什么与请求构造同源，渠道限制用用户负责的供应商模型上的能力覆盖表达。成片是否有声与能否下发音轨开关是独立的维度。能力求值见 [图片](../../lib/backends/image_capabilities.py)与 [视频](../../lib/backends/video_capabilities.py)。时长求值失败及端点固定时长的处理见 [ADR 0086](../adr/0086-video-request-facts-evaluated-once.md)。

以下写法都是在来源之外另写一份能力数值：另起常量重复某个档位；调用方或配置界面在来源缺值时自行补默认值；调用通道再写一份与条目重复的时长或分辨率检查；`agent_runtime_profile/` 与 `lib/prompts/` 的模板硬编码时长、分辨率档位，而不是由编排层从上述来源注入。供应商或目录数据调整后，两份数据迟早不一致：用户能选的档位与实际请求不符，或者 Agent 按旧档位规划出执行期申请不到的时长。

放行条件：

- 端点级时长校正与来源缺省策略按 [ADR 0104](../adr/0104-capability-sources-split-by-dimension.md) 和 [ADR 0105](../adr/0105-model-name-to-entry-matching.md) 的约定处理，例如 Vidu 端点级时长校正、未匹配条目时的保守回落。
- 过渡期保留的副本：须有一致性测试锁定两份数据，且在 PR 中写明负责删除该副本的 issue。
- 提示词中描述叙事节奏的经验秒数（如「全片平均 3–5 秒/分镜」）不属于能力档位。

## 续跑

### 续跑已提交的任务按提交时冻结的上下文执行，新增的调用前判定区分新调用与续跑

续跑只向厂商取回已提交任务的结果，不发起新的生成，却与新调用共用解析、装配与请求链。链上按当下状态做的判定或改写会一起作用到续跑上：模型下线拦截把关停前已提交的任务拦下；按当下 base 路径识别请求的路径覆盖认不出续跑请求。两种情况下，用户已付费的结果都取不回。

给共用链路新增调用前拦截、或按当下配置改写请求时，由续跑入口显式声明调用性质，声明下按 payload 锁定的提交时身份与上下文判定；新提交不带声明，照常拦截。参考 `server/services/tasks/generation_context.py` 的 `VideoLaneRequest.submitted_job` 及其传到 `assemble_backend` 的同名参数，以及 `lib/custom_provider/path_overrides.py` 按提交时的 base 匹配。同时补一条续跑用例，覆盖提交之后生命周期或配置改变的情形。
