# 默认界面语言不改为特定市场语言

ArcReel 上游不把前端默认语言（`fallbackLng` 与请求头 `Accept-Language` 的回退值）改为某个特定市场的语言，也不关闭按浏览器语言自动识别界面语言。

## Why this is out of scope

界面语言目前按「用户显式选择 → 浏览器语言 → 回退语言」的顺序确定（`frontend/src/i18n/index.ts` 的 `LanguageDetector`）。没有显式选择过语言的用户，界面跟随浏览器语言；回退语言只在浏览器语言不在 `SUPPORTED_LANGUAGES` 中时生效。

把回退语言换成某个市场语言、同时去掉浏览器识别，会让所有没有手动选过语言的现有用户在升级后看到另一种语言的界面，英文与中文浏览器用户也失去自动匹配。这是面向单一市场的发行定制，不是对全体用户的改进。

需要某种语言优先的部署可以在自己的 fork 或发行版中调整回退语言，不需要改动上游。新增或完善某种界面语言的翻译不属于本条，仍然欢迎贡献。

## Prior requests

- PR #3051：refactor: separate visual and dialogue prompts across languages（其中的越南语默认界面部分）
