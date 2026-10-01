---
paths:
  - "website/docs/**"
  - "README.md"
  - "README.en.md"
  - "CONTRIBUTING.md"
---

# 用户文档

### README 只回答「是什么、适合谁、和直接调用模型 API 有什么区别、如何最快运行起来」

README 面向第一次访问仓库的人。具体模型名、单价与接口参数放到文档站对应页面；写进 README 的这类信息会随供应商每次更新而过期。

### 供应商信息描述能力与配置方式，具体型号以设置页与供应商官方文档为准

文档说明覆盖哪些媒体类型、ArcReel 如何统一配置、不同能力怎么选、具体信息去哪里确认。在文档里罗列型号与价格的段落会先于代码过期。

### 需要 JSX 或 import 的页面用 `.mdx`

`website/docusaurus.config.ts` 设了 `markdown.format: "detect"`，`.md` 按 CommonMark 解析。在 `.md` 里写 JSX 或 import 都不会报编译错误：JSX 标签被当作原始 HTML 原样输出（带子内容的标签，子内容直接显示成页面文本），import 语句被当作普通文本显示。

### 站内页面互相引用时用相对文件路径，指向未上站的仓库文件用 GitHub 绝对链接

相对路径（如 `../ops/deployment.md`）由构建期校验，改名时构建失败，不会在线上留下断链；未上站的仓库文件在站点上不存在，相对路径必然 404。`CONTRIBUTING.md` 会同步为站点的开发区页面，它引用仓库内文件时同样用 GitHub 绝对链接。
