# 随版声明式内置端点

本目录存放以声明式定义实现的**内置调用端点**。每份 `<key>.json` 的文件名即内置键（如
`newapi-video.json` → `newapi-video`），由 `lib/custom_provider/builtin_definitions.py` 在
import 期读入 `ENDPOINT_REGISTRY`。

约束：

- 定义须过共享校验器 `arcreel_market_core.endpoint_definition.validate_definition`，
  `meta.author` 为 `ArcReel`，键不得以 `ce-` 开头。任一条不满足即 import 期抛错、进程起不来。
- 定义不落库、用户不可编辑删除；升级换文件即生效，用户「复制为我的」产出的 `ce-<id>` 副本不跟随。
- 键与 Python 内置端点共用同一命名空间，文件名不得与注册表里已有的键重复。

## 运行时已核对的表达力

以下两条是声明式运行时现有的行为，写新定义前不必改运行时：

- **轮询用 POST。** 提交、轮询、二次取件三节的 `method` 都可取 `GET`、`POST`、`PUT`；轮询请求可以带
  `body`，`body` 中可以引用 `{{ task_id }}`。硅基流动视频这类「轮询用 POST」的上游可以直接写成定义。
- **任务 id 含 `/`。** URL 模板里的占位符不做 percent-encoding，含 `/` 的任务 id 原样展开为多级路径：
  `{{ base_url }}/v1beta/{{ task_id }}` 遇到 Veo 的 operation name `models/veo/operations/abc` 时展开为
  `/v1beta/models/veo/operations/abc`。需要把 `/` 编码成 `%2F` 的上游不支持；目前尚未发现这样的上游
  （new-api 会先把 operation name 编码成自己的任务 id）。

前端「新建」表单的示例模板不放这里——它是预填内容而非内置端点，见
`frontend/src/data/example-templates/`。
