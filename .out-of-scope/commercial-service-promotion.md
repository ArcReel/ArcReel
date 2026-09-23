# 以推广商业服务为目的的接入

ArcReel 不经 PR 接受以推广某项商业服务为主要目的的贡献：为某家第三方服务新增内置调用端点或供应商接入，或在文档中加入该服务的推荐与链接。这类合作经 support@arc-reel.com 洽谈，不走代码贡献流程。

## Why this is out of scope

随版内置调用端点只用于收编 ArcReel 已有的内置实现（`docs/adr/0067`）。长尾聚合站与中转站属于「写定义」的范围：用户在「调用端点」中自行编写或导入声明式定义即可接入，不需要改动代码。把单家服务写进注册表，发版与维护成本由 ArcReel 承担，也会为其他同类服务留下先例。

官方市场源同样不收这类投稿（`ArcReel/arcreel-market` 的 `CONTRIBUTING.md` 内容准则）。

判断看贡献的主要目的，而非是否涉及商业服务：修复已有内置供应商的缺陷、或把 ArcReel 已有的 Python 内置 backend 收编为声明式定义，不属于本条。

## Prior requests

- PR #2500 — feat: add MuAPI video endpoint
