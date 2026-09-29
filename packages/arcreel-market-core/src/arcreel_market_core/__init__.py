"""ArcReel 市场源工具与端点定义校验器。

市场源侧（``market``）：地址解析、索引 schema 与投影、条目校验、索引生成、图标检查与目录校验；
端点定义（``endpoint_definition`` 及其依赖）：结构与语义两层校验。诊断一律是 ``code + params``，
本包不携带翻译目录，渲染由调用方传入 translator。
"""
