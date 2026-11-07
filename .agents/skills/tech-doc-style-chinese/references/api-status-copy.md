# API status and error copy

Read this file only when writing, translating, or reviewing API documentation, status labels, error codes, and interface notices.

## Basic principles

- Confirm the status's actual meaning in the current interface before choosing Chinese wording.
- Do not map one English status word to a single fixed Chinese translation.
- Preserve distinctions between HTTP statuses, business statuses, task statuses, and UI notices.
- Where possible, error copy explains the problem, impact, and recovery method.
- Do not modify status codes, enum values, field names, or original response text.

## Common status words

- `Success`
  - Depending on meaning, use `已完成`、`已处理`、`请求成功`
- `Invalid`
  - Use `无效`、`格式有误`、`校验未通过`
  - Do not translate by default as `非法`
- `Available`
  - Use `已开通`、`可获取`、`可访问`、`可使用`
- `Unsupported`
  - Use `暂不支持`、`不支持该类型`、`当前不支持`
- `Pending`
  - Use `待处理`、`处理中`、`待确认`
- `Expired`
  - Use `已过期`
- `Missing`
  - Use `缺少`、`未提供`
- `Denied`
  - Use `被拒绝`、`未通过`
- `Forbidden`
  - Depending on meaning, use `无权限访问`、`当前不允许访问`
- `Not Found`
  - Use `未找到对应内容`、`未找到对应数据`
- `Accepted`
  - Use `已受理`、`已接收，等待处理`
- `Completed`
  - Use `已完成`
- `Failed`
  - Use `失败`、`处理失败`
- `Conflict`
  - Use `状态冲突`、`资源状态不一致`、`请求与当前状态冲突`
- `Unauthorized`
  - Depending on the actual cause, use `未登录`、`认证未通过`、`缺少认证信息`
  - Do not mechanically translate as `未授权`
- `Bad Request`
  - Use `请求参数有误`、`请求格式有误`
- `Timeout`
  - Use `请求超时`、`处理超时`

## Parameter descriptions

Where possible, state for each parameter:

- Type and format
- Whether required
- Unit
- Default value
- Allowed values or range
- Behavior when omitted
- Dependencies or mutual exclusions with other parameters

Avoid non-actionable descriptions such as “正常值”, “根据情况填写”, or “相关信息”.

## Error-copy structure

Prefer providing information in this order:

1. What happened
2. Which object is affected
3. Possible causes
4. Actionable recovery steps
5. Diagnostic information to supply if failure persists

Do not present speculation as a definite cause or promise recovery times without evidence.
