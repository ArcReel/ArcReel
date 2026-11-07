# Do not change the default UI language for one market

Upstream ArcReel does not switch frontend fallback language (`fallbackLng` and the `Accept-Language` header fallback) to a particular market’s language or disable browser-language detection.

## Why this is out of scope

UI language follows explicit user choice → browser language → fallback (`LanguageDetector` in `frontend/src/i18n/index.ts`). Without an explicit choice, the UI follows the browser; fallback applies only when the browser language is absent from `SUPPORTED_LANGUAGES`.

Changing fallback to a market language while removing detection switches existing users without explicit choices to another language after upgrade, also removing automatic matching for English/Chinese browsers. That customizes a single-market distribution instead of improving the product for everyone.

Language-prioritized deployments may adjust fallback in their own fork/distribution without upstream changes. Adding/completing UI translations is outside this prohibition and remains welcome.

## Prior requests

- PR #3051: refactor: separate visual and dialogue prompts across languages (the Vietnamese-default UI portion)
