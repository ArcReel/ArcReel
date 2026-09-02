# 故障处理

条件与处置一一对应,均暂停询问委派方。唯一自裁决的是**能力证伪**:reviewer 的回复或官方通知确证其无法参审——App 未接入、要求创建 / 连接账号、服务已停止——该家本 PR 按不参审处理、不再触发,记入退出汇报;沉默或一般报错不算证伪,仍按下列条目暂停。

- **某家 reviewer(含 CodeQL 分析)超过 30 分钟未响应**:bot 可能服务异常或配额已满,暂停说明现状。Gemini fix-up 顺延导致的「未审」是设计内跳过,不算无响应
- **bot 报错**(如 "Internal error"、"Token limit exceeded"):贴出错误内容,按 reviewers.md 该家的触发约束询问是否重跑
- **`quota_alerts` 非空**:alert 之后该家已有成功审查(更晚的 review 或 walkthrough 更新)的视为已恢复,忽略残留 banner;真实受阻时,reviewers.md 该家有专项配额处置段的(如 CodeRabbit)按其规则自行处置,不暂停;其余家贴出 `body_head`,询问停用该家继续其他家,还是等 quota 恢复后再 push
- **`codeql_checks.failing` 非空**(失败态集合见 poll.sh header `checks_failing` 条):分析失败,alerts 数据停留在上次成功分析,不能做终核;询问是否重跑失败的 workflow
- **`security_alerts.available == false`**:贴出 `unavailable_hint`,按 reviewers.md「仓库未接入」段判别权限问题与未接入——两种情形都需委派方确认,不得自动跳过 security 门槛
- **`wait.sh` 返回 `WAIT_ERROR`**:401 / 403 按下条处理;其余贴出 stderr 暂停
- **`gh` 401 / 403**:请委派方运行 `gh auth refresh -s repo`
- **review 评论语义模糊**,按 `receiving-code-review` 的纪律仍无法判定是否 pushback:贴出原文请委派方定夺
