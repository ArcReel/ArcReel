# 随版声明式内置端点

本目录存放以声明式定义实现的**内置调用端点**。每份 `<key>.json` 的文件名即内置键（如
`newapi-video.json` → `newapi-video`），由 `lib/custom_provider/builtin_definitions.py` 在
import 期读入 `ENDPOINT_REGISTRY`。

约束：

- 定义须过共享校验器 `lib.custom_provider.endpoint_definition.validate_definition`，
  `meta.author` 为 `ArcReel`，键不得以 `ce-` 开头。任一条不满足即 import 期抛错、进程起不来。
- 定义不落库、用户不可编辑删除；升级换文件即生效，用户「复制为我的」产出的 `ce-<id>` 副本不跟随。
- 键与 Python 内置端点共用同一命名空间，文件名不得与注册表里已有的键重复。

前端「新建」表单的示例模板不放这里——它是预填内容而非内置端点，见
`frontend/src/data/example-templates/`。
