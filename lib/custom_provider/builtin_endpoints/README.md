# 随版声明式调用端点

本目录下的 `<key>.json` 随 ArcReel 版本一同发布，文件名去掉扩展名即内置调用端点的键，例如 [`newapi-video.json`](newapi-video.json) 对应 `newapi-video`。内置定义只读，并随版本升级自动更新；点击「复制为我的」可基于它创建一份独立的自定义调用端点。

## 添加端点

1. 从[端点 API 索引](../../../docs/api-docs/endpoints/README.md)查找官方协议资料，按[声明式定义](../../../docs/adr/0067-custom-endpoint-declarative-definition.md)编写端点文件，并将 `meta.author` 设为 `ArcReel`。
2. 选用未被[内置端点注册表](../endpoints.py)占用的键。`ce-` 前缀保留给自定义调用端点。[定义加载器](../builtin_definitions.py)会在注册时校验定义内容、作者和键名，校验不通过将导致应用启动失败。
3. 在仓库根目录运行随版定义校验：

```bash
uv run python -m pytest tests/unit/lib/custom_provider/test_builtin_endpoint_definitions.py
```

轮询支持使用 `POST`，也可以在请求体中引用 `{{ task_id }}`。URL 路径中的占位符按原值展开，例如 `models/veo/operations/abc` 中的 `/` 会原样保留。具体渲染规则见[共享模板引擎](../../../packages/arcreel-market-core/src/arcreel_market_core/endpoint_definition/template_engine.py)。

前端「新建自定义调用端点」的预填定义放在[示例模板](../../../frontend/src/data/example-templates/)目录。
