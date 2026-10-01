---
name: translate-docs
description: Translate every dirty ArcReel documentation source into English and refresh the translation lockfile.
disable-model-invocation: true
---

# Translate Docs

Run this workflow from the repository root. Translate prose directly; the bundled scripts discover source/target pairs, record source fingerprints, and check the mechanical invariants.

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
- `stale`: align the existing target with the current source. Targets are often hand-maintained alongside their sources, so a stale fingerprint can mean anything from one changed sentence to a rewritten page; compare the two section by section, keep wording that is still faithful, and rewrite what has drifted.
- `frontmatter`: make the target's untranslated frontmatter match the source (see Step 3).
- `orphan`: delete the reported target if it exists; the later `record` command removes the obsolete lock entry.

Finish discovery only after every item has an explicit action. The lockfile changes only through `record`.

For a large batch, dispatch subagents by file group. Give each one its exact source → target pairs and point it at Steps 2 and 3 of this file.

## 2. Load terminology

Before translating prose, search these truth sources for established English product terms:

- Frontend English locale: `frontend/src/i18n/en/`
- `README.en.md`

These sources are the glossary; reuse their exact terminology, including UI labels quoted in prose.

When those sources conflict, use the official name a reader outside China would recognize for the same product, not the name of a different product. Documentation translates `阿里百炼` as `DashScope`, matching `README.en.md`'s established usage; the frontend locale's per-endpoint `Alibaba Model Studio` labels do not govern documentation.

Product identity itself is invariant across target languages. Translate `剪映` as `Jianying`: CapCut is a separate international product, and ArcReel has not verified draft compatibility with it. `CapCut` appears only where a page states that distinction.

## 3. Translate every dirty source

Translate natural-language prose and link text into clear technical English. Preserve the document's information, tone, section order, lists, tables, and formatting.

Apply these invariants to every file:

- In frontmatter, translate only values of `title`, `description`, and `sidebar_label`. Copy every other key and value from the source exactly; `status` reports a `frontmatter` item and `record` refuses to run until they match.
- Preserve inline code exactly.
- Inside fenced code blocks, preserve the executable substance exactly: commands, program output, identifiers, configuration keys, and paths or filenames that other software really produces. Translate the human-readable text a reader is meant to read: diagram node labels, comments, instructional placeholder values, and fences that hold prose rather than code. A literal repository convention written in Chinese stays in Chinese — Chinese commit-message examples, changelog section names, and placeholders such as `<中文理由>` describe what a contributor must actually type.
- Translate human-readable link text. Point each link destination at its English counterpart when one exists; otherwise keep the destination exactly:
  - A heading inside the same document: link to the target document's own heading. `README.md` has no explicit anchor IDs, so `README.en.md` links to the English heading slug — `#快速开始` becomes `#quick-start`.
  - The documentation site: the default locale is unprefixed, so every `docs.arc-reel.com/...` destination gains an `/en/` prefix (`https://docs.arc-reel.com/guide/...` becomes `https://docs.arc-reel.com/en/guide/...`).
  - An external page with an English edition: link to that edition, such as a repository's `README.en.md` in place of its `#readme`.
- Preserve `:::` admonition marker lines exactly. Translate prose inside the admonition.
- Preserve explicit anchor IDs such as `{#deployment}` exactly. Translate their headings.
- Keep product names, command names, paths, configuration keys, environment variables, identifiers, and version constraints unchanged.

The lock pipeline currently registers English targets only: `targetForSource` in `translation-lock.mjs` maps every source into `website/i18n/en/`. Before adding another documentation locale, extend that forward mapping first — otherwise every file of the new locale is reported as an unregistered orphan and `record` refuses to run.

`README.md` maps to `README.en.md`. `CONTRIBUTING.md` maps to `website/i18n/en/docusaurus-plugin-content-docs/current/dev/contributing.md`; base that target on the synchronized `website/docs/dev/contributing.md` so its generated frontmatter and `{#contributing}` anchor remain intact. The lockfile records `CONTRIBUTING.md` as the source key but fingerprints that synchronized copy's content, not the root file's, so a `sync-contributing.mjs` change alone can also mark the target stale. Other Markdown sources use the exact targets printed by `status`.

Finish this step only when every `orphan` target is gone and every `missing` or `stale` target is a complete English rendering: each source section has its counterpart in the target, and the target holds nothing the source lacks.

## 4. Refresh UI translations

Generate the current Docusaurus message inventory, then check it:

```bash
set -euo pipefail
cd website
pnpm write-translations --locale en
cd ..
node .claude/skills/translate-docs/scripts/ui-messages.mjs
```

`ui-messages.mjs` removes the generated footer `copyright` key and lists every English `message` value still carrying Chinese text. Translate each listed message in place, preserving JSON keys, `description` values, and placeholders such as `{count}` exactly. Finish when the script prints `UI messages are translated.`

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

`record` refuses to update the lockfile while a target is missing, while a target's untranslated frontmatter differs from its source, and while any Markdown file under `website/i18n/*/docusaurus-plugin-content-docs/current/` has no source mapping to it. Completion requires `status` to print `Translations are up to date.`, typecheck to pass, and the build to emit both `build/` and `build/en/` without broken links or anchors.
