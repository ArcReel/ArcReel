# arcreel-market-core

ArcReel 的市场源工具与端点定义校验器。ArcReel 应用、市场源仓库的 CI 与官方服务共用这一份实现，
同一份定义在三处得到同样的判定。

## 与 `arcreel-market` 的关系

[`ArcReel/arcreel-market`](https://github.com/ArcReel/arcreel-market) 是官方市场源仓库，条目以
`endpoints/<slug>/definition.json`（外加可选的 `icon.<png|webp|svg>`）的目录约定存放，根目录的
`arcreel-market.json` 是由这些目录生成的索引。本包负责：

- 按目录生成索引（`generate`）；
- 按全部规则校验市场源目录（`check`）：索引结构、slug、旁置文件与图标、条目定义、索引条目与定义
  `meta` 的投影一致性、版本门槛格式；
- 供客户端使用的市场源地址解析、索引解析与远程抓取边界。

市场源仓库不维护任何校验逻辑。它的 PR 校验与索引发布调用 ArcReel 仓库的两条可复用工作流
（`market-validate.yml`、`market-publish-index.yml`），工作流安装本包，通过主仓的
`scripts.market` 注入应用翻译，运行上面两个命令。第三方
市场源（fork 或自建）用同样的方式接入。

## 为什么包含视频后端契约等叶子模块

端点定义校验器有两层：`schema.json` 管结构，语义层检查占位符、取值路径、状态映射与能力声明。
语义层依赖几个与市场无关的叶子模块，因此一并收在本包：

| 模块 | 校验器用它做什么 |
|---|---|
| `video_backend_contract` | 供应商任务状态的四档归一、参考音频模式及其与数量的一致性判定 |
| `aspect_size` | 模板引擎按画幅与分辨率档位推导宽高 |
| `auth_section` | 两种 `kind` 共用的 `auth` 节模板原语与检查 |
| `definition_diagnostics`、`definition_schema_errors` | 诊断载体与 jsonschema 报错的归一 |
| `comfyui` | `kind: comfyui` 定义的结构契约与语义判定 |
| `validation_messages` | locale-neutral 的消息载体 |

这些模块只依赖标准库与本包的第三方依赖，不依赖 ArcReel 应用的任何部分；ArcReel 应用在运行时直接
导入它们，不另存副本。

## 诊断与翻译

诊断一律是稳定码加参数（`ValidationMessage(key, params)`），本包不携带翻译目录。渲染由调用方传入
translator；没有翻译目录的边界（本包的 CLI、异常文本）用 `code_translator` 输出键名与参数本身。
本包能产出的全部消息键见 `arcreel_market_core.message_keys.MESSAGE_KEYS`，ArcReel 应用的
zh / en / vi 目录覆盖其中每一个键，由应用侧的一致性测试保护。

## 使用

命令行：

```bash
python -m arcreel_market_core generate <市场源目录> [--name ...] [--default-name ...] [--dry-run]
python -m arcreel_market_core check <市场源目录>
```

退出码非零即不通过，诊断逐行输出为 `文件:定位: [code] key(params)`，汇总同样输出键名与参数。
在 ArcReel 仓库内需要自然语言诊断时，使用工作流同款入口：

```bash
python -m scripts.market [--locale zh|en|vi] check <市场源目录>
python -m scripts.market [--locale zh|en|vi] generate <市场源目录>
```

默认中文；`arcreel_market_core.market.cli.main` 也接受 `translate=`，由宿主注入自己的 translator。

作为依赖引入（以 git tag 固定版本）：

```bash
pip install "arcreel-market-core @ git+https://github.com/ArcReel/ArcReel@<tag>#subdirectory=packages/arcreel-market-core"
```

```python
from arcreel_market_core.endpoint_definition import validate_definition
from arcreel_market_core.market import check_source

diagnostics = validate_definition(definition)
payload = diagnostics.to_payload(translate)  # {"errors": [{"path", "code", "message"}], "warnings": [...]}
```

## 开发

本包是 ArcReel 仓库 uv workspace 的成员，测试在本目录的 `tests/` 下，与应用测试分开运行：

```bash
uv run python -m pytest packages/arcreel-market-core/tests
```
