---
status: accepted
---

# 项目媒体使用文件 mtime 作为浏览器缓存的版本键

会话级计数器在页面刷新后会失效，而固定 URL 也可能指向已经更新的文件内容。项目资产使用文件的纳秒 mtime 形成 URL 版本键，版本历史文件与带版本键的项目文件允许浏览器长期缓存。没有版本键的可变 URL 每次再验证。项目 API 与事件提供更新后的键。文件 mtime 仅用于缓存寻址，产物时效中的内容依据另见 [ADR 0062](0062-artifact-lifecycle-and-currency.md)。
