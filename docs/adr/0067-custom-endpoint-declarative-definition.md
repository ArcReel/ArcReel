---
status: accepted
---

# 自定义调用端点采用声明式定义，统一「JSON in/out + 提交/轮询」流派

接入一种新视频协议此前只有改源码一条路：`lib/custom_provider/endpoints.py` 硬编码全部内置调用端点，新协议要新建 backend 类、注册 endpoint 键、加推断规则与 i18n。而长尾聚合站与中转站的协议同属一个流派——JSON 请求进、JSON 响应出、提交后轮询——只是字段命名与取值路径各异，逐家写 Python 不可持续。

我们决定**自定义调用端点采用声明式定义**：一份 JSON 容器（`kind: declarative`、文件级 `schema_version`），请求用 JSON 结构模板 + `{{ }}` 占位符（整串单占位符保留原生类型）与少量固定构造（`$each` 铺字段、对象级 `$when` 守卫、可选 `result` 二次取件节），响应提取用 RFC 9535 JSONPath 受限子集的优先级数组，状态与枚举映射为纯字典；不引入 Jinja2 等通用模板引擎。定位不是表达一切协议，而是**「JSON in/out + 提交/轮询」流派的统一形态**：向下覆盖长尾聚合站与中转站（10 家抽样 8 家可表达全链路），向上把可整体表达的 HTTP 式内置 backend 收编为随版声明式定义（首批 newapi / v2_video_generations / minimax，上线同 PR 删除对应 Python 路径）。不可表达的协议——签名类鉴权（JWT / SigV4）、按素材路由、跨列表配对、SDK 式接入——留在 Python 内置 endpoint，不为其扩格式。

**明确不采用**：① **通用覆盖率标尺下弃用声明式**——以「表达全部协议」衡量，声明式只有五六成覆盖率；但把范围圈定在流派内后，覆盖率由 22 份定义复刻 8 家 HTTP 式内置 backend 的验收证实，调试劣势由端点测试三模式（预览请求 / 验证响应 / 测试连接）与保存共用的校验器承接。② **运行时 Python plugin 作为自定义协议的接入形态**——插件的加载、执行与信任模型是独立议题（#872），容器以 `kind` 字段为其预留槽位；声明式收编的是本可整体表达的那部分，不与 plugin 互斥。③ **为不可表达构造扩充格式**（签名鉴权、按素材分支、multipart 等）——可选构造「不写就不见」的原则下，每项扩充都以外部长尾的真实需要为准入，而非追求格式完备。

## 存储上的两项偏离

- **定义本体整份存 JSON**：新表 `custom_endpoint` 以 `definition` 一列 JSON 为唯一真相源。这不与 ADR 0042「字段集稳定优先定型列」相抵——定义随 `schema_version` 演进、字段集不稳定，正是 0042 留给 JSON 的「真正动态」场景；定型列只提 DB 层不解析 JSON 就要用的只读镜像（`kind` / `schema_version` / `media_type` / `display_name` / 时间戳），写入时由定义派生，不构成第二真相源。
- **被模型行引用则拒删**：删除被 `custom_provider_model` 引用的自定义调用端点返回 409 与引用清单。这是仓库首个「拒删」先例，与既有「删 + 清悬空引用」路径并存而非取代：模型行承载用户手工配置（定价、能力覆盖），级联删除丢用户劳动，留悬空引用则把错误推迟到生成时才爆。引用完整性由服务层计数保证，不加 FK 约束——409 要回引用清单，约束只能抛一个 `IntegrityError`，而删除路径本就要先查引用。

## Consequences

- 内置调用端点的实现形态从此有两种：Python backend 或随版声明式定义（`lib/custom_provider/builtin_endpoints/*.json`，import 期入注册表、沿用内置键、不落 DB）；同键换实现，在途任务由声明式运行时按键接手，无版本锁。
- 新增 HTTP 式提交/轮询协议的默认路径变为「写定义」（UI 或导入，零代码），确认不可表达再落 Python；catalog 以 `kind: python | declarative` 区分实现形态。
- 后续 PR 若要为不可表达构造扩充格式、或以声明式吞并 SDK 式 backend，须先修订本 ADR 的流派边界。
- `docs/research/arcreel-video-api-protocol-research.md` 第 7 章的接入方案选型以本 ADR 为准（该文档存修订注）。
