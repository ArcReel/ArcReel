---
name: translate-docs
description: Translate every dirty ArcReel documentation source into English and refresh the translation lockfile.
disable-model-invocation: true
---

# Translate Docs

Run this workflow from the repository root. Translate prose directly; the bundled script only discovers source/target pairs and records source fingerprints.

## 1. Discover the batch

Synchronize the generated contributing copy first, so Step 3's `CONTRIBUTING.md` translation always reads current content rather than a stale or absent file:

```bash
set -euo pipefail
cd website
pnpm sync-contributing
cd ..
node .claude/skills/translate-docs/scripts/translation-lock.mjs status
```

Treat every reported item as one batch:

- `missing`: create the reported target from the complete source.
- `stale`: retranslate the complete source into the reported target.
- `orphan`: delete the reported target if it exists; the later `record` command removes the obsolete lock entry.

Finish discovery only after every item has an explicit action. Never hand-edit `website/i18n/translation.lock.json`.

## 2. Load terminology

Before translating prose, search these truth sources for established English product terms:

- Frontend English locale: `frontend/src/i18n/en/`
- `README.en.md`

Reuse their exact terminology. Do not create a separate glossary.

When those sources conflict, use the official name a reader outside China would recognize for the same product — never the name of a different product. Documentation translates `阿里百炼` as `DashScope`, matching `README.en.md`'s established usage; the frontend locale's per-endpoint `Alibaba Model Studio` labels do not govern documentation.

Product identity itself is invariant across target languages. Keep `剪映` as `Jianying`: CapCut is a separate international product, and ArcReel has not verified draft compatibility with it. Use `CapCut` only when stating that distinction, never as a translation of `剪映`.

## 3. Translate every dirty source

Translate natural-language prose and link text into clear technical English. Preserve the document's information, tone, section order, lists, tables, and formatting.

Apply these invariants to every file:

- In frontmatter, translate only values of `title`, `description`, and `sidebar_label`. Preserve `id`, `slug`, every other key, and all non-translated values exactly.
- Preserve inline code exactly.
- Inside fenced code blocks, preserve the executable substance exactly: commands, program output, identifiers, configuration keys, and paths or filenames that other software really produces. Translate the human-readable text a reader is meant to read: diagram node labels, comments, instructional placeholder values, and fences that hold prose rather than code. A literal repository convention written in Chinese stays in Chinese — Chinese commit-message examples, changelog section names, and placeholders such as `<中文理由>` describe what a contributor must actually type.
- Preserve URL destinations exactly. Translate human-readable link text.
- A link to a heading inside the same document is an exception: point it at the target document's own heading. `README.md` has no explicit anchor IDs, so `README.en.md` links to the English heading slug — `#快速开始` becomes `#quick-start`. Never inject an `<a id>` tag to keep a Chinese fragment alive.
- A link to the documentation site is another exception: `docs.arc-reel.com` destinations in a Chinese source have no locale prefix because the default locale is unprefixed. Every English target — `README.en.md` and every translated page under `website/i18n/en/` — must route English readers to the English site, so give each `docs.arc-reel.com/...` destination an `/en/` prefix (`https://docs.arc-reel.com/guide/...` becomes `https://docs.arc-reel.com/en/guide/...`).
- Preserve `:::` admonition marker lines exactly. Translate prose inside the admonition.
- Preserve explicit anchor IDs such as `{#deployment}` exactly. Translate their headings.
- Keep product names, command names, paths, configuration keys, environment variables, identifiers, and version constraints unchanged.

`README.md` maps to `README.en.md`. `CONTRIBUTING.md` maps to `website/i18n/en/docusaurus-plugin-content-docs/current/dev/contributing.md`; base that target on the synchronized `website/docs/dev/contributing.md` so its generated frontmatter and `{#contributing}` anchor remain intact. The lockfile records `CONTRIBUTING.md` as the source key but fingerprints that synchronized copy's content, not the root file's, so a `sync-contributing.mjs` change alone can also mark the target stale. Other Markdown sources use the exact targets printed by `status`.

Finish this step only when every `missing` or `stale` target is a complete English rendering and every `orphan` target is gone.

## 4. Refresh UI translations

Generate the current Docusaurus message inventory, then translate any Chinese `message` values in the English JSON files:

```bash
set -euo pipefail
cd website
pnpm write-translations --locale en
cd ..
```

Maintain every generated English JSON file, including `code.json`, navbar/footer JSON, and `docusaurus-plugin-content-docs/current.json`. Preserve JSON keys, `description` values, and placeholders such as `{count}` exactly. Finish when the English inventory has every generated key and no user-facing Chinese message remains.

Delete `footer.json`'s generated `copyright` key. It snapshots `themeConfig.footer.copyright`'s `new Date().getFullYear()` as a static string at generation time, which would permanently override the dynamic year for the English locale; removing the key lets the English footer fall back to the same dynamic config the default locale uses.

## 5. Record and verify

After all translations are complete, record LF-normalized SHA-256 fingerprints and verify the batch is clean:

```bash
set -euo pipefail
node .claude/skills/translate-docs/scripts/translation-lock.mjs record
node .claude/skills/translate-docs/scripts/translation-lock.mjs status
cd website
pnpm typecheck
pnpm build
```

`record` refuses to update the lockfile while a target is missing, and while any Markdown file under
`website/i18n/*/docusaurus-plugin-content-docs/current/` has no source mapping to it. Completion requires `status` to print `Translations are up to date.`, typecheck to pass, and the build to emit both `build/` and `build/en/` without broken links or anchors.
