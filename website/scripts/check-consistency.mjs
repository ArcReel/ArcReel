#!/usr/bin/env node
// CI consistency gate: update-docs page inventory / orphan translations / explicit published-heading anchors / complete UI JSON keys.
// Any violation exits nonzero. Missing/stale translations are outside this script (translation-lock.mjs status covers them,
// written to step summary by a separate workflow step without blocking builds).

import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { scanMarkdownLines, walkMarkdownFiles } from "./markdown-scan.mjs";
import { checkUpdateDocsInventory } from "./update-docs-inventory.mjs";

const websiteDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(websiteDir, "..");

function toPosix(path) {
  return path.split(sep).join("/");
}

// ---- 1. update-docs page inventory and CONTRIBUTING page-responsibility list ----

function checkDocInventory() {
  return checkUpdateDocsInventory(repoRoot).problems;
}

// ---- 2. Orphan translations: delegate to the translation skill’s single source of truth (.claude/skills/translate-docs/), without reimplementation. ----

function checkOrphanTranslations() {
  const lockScript = resolve(repoRoot, ".claude/skills/translate-docs/scripts/translation-lock.mjs");
  const output = execFileSync(process.execPath, [lockScript, "status", "--root", repoRoot, "--json"], {
    encoding: "utf8",
  });
  const orphans = JSON.parse(output).filter((item) => item.state === "orphan");
  // Orphans come from lockfile entries whose source was deleted, or unregistered translation files discovered by reverse scans.
  // The latter infer a possibly nonexistent source from the target path, so wording must not assert that the source no longer exists.
  return orphans.map((item) => `孤儿译文：${item.target}（源 ${item.source} 未登记或已不存在）`);
}

// ---- 3. Published headings missing explicit anchors ----
//
// Every heading needs `{#id}` (locales share anchors as link targets). Translations share source structure and rules:
// scanning only sources would let translated headings without `{#id}` escape until a build-time onBrokenAnchors check happens to find a link targeting them.
//
// Homepage card headings in index.mdx use JSX `<h2>`, where `{#id}` syntax cannot add anchors. Explicitly register
// exempt files rather than silently ignoring JSX headings: new .mdx files with unregistered JSX headings fail here, requiring
// reviewers to choose exemption or Markdown headings. Registered files without JSX headings also fail (stale entries
// must be removed). Paths are relative to document roots; matching source and locale copies share exemptions.
const JSX_HEADING_EXEMPT_DOCS = new Set(["index.mdx"]);

const ANCHOR_SUFFIX = /\{#([a-z0-9-]+)\}$/;

const TRANSLATION_DOCS_SUFFIX = "docusaurus-plugin-content-docs/current";

// Document roots: Chinese sources and each locale translation directory under i18n. New locales are included automatically.
function docRoots() {
  const i18nDir = resolve(websiteDir, "i18n");
  if (!existsSync(i18nDir)) return ["docs"];
  const localeRoots = readdirSync(i18nDir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => `i18n/${entry.name}/${TRANSLATION_DOCS_SUFFIX}`)
    .filter((root) => existsSync(resolve(websiteDir, root)))
    .sort();
  return ["docs", ...localeRoots];
}

function checkAnchors() {
  const problems = [];
  const exemptDocsFound = new Set();

  for (const root of docRoots()) {
    const jsxHeadingDocs = new Set();
    const docPaths = new Set();

    for (const file of walkMarkdownFiles(websiteDir, root)) {
      const docPath = toPosix(relative(root, file));
      docPaths.add(docPath);
      const seenAnchors = new Set();

      for (const { index, line, hashes, text, hasJsxHeading } of scanMarkdownLines(
        readFileSync(resolve(websiteDir, file), "utf8"),
      )) {
        if (hasJsxHeading) jsxHeadingDocs.add(docPath);
        if (hashes === null) continue;

        const anchorMatch = ANCHOR_SUFFIX.exec(text);
        if (!anchorMatch) {
          problems.push(`${file}:${index + 1} 标题缺少显式锚点 {#id}：${line.trim()}`);
          continue;
        }
        const anchor = anchorMatch[1];
        if (seenAnchors.has(anchor)) {
          problems.push(`${file} 锚点 id「${anchor}」在同页内重复`);
        }
        seenAnchors.add(anchor);
      }
    }

    for (const docPath of jsxHeadingDocs) {
      if (!JSX_HEADING_EXEMPT_DOCS.has(docPath)) {
        problems.push(
          `${root}/${docPath} 含 JSX 标题标签但 ${docPath} 未登记在 check-consistency.mjs 的 ` +
            "JSX_HEADING_EXEMPT_DOCS 中——要么改回带 {#id} 的 Markdown 标题，要么显式登记豁免",
        );
      }
    }
    for (const docPath of JSX_HEADING_EXEMPT_DOCS) {
      if (!docPaths.has(docPath)) continue;
      exemptDocsFound.add(docPath);
      if (!jsxHeadingDocs.has(docPath)) {
        problems.push(
          `${root}/${docPath} 已不含 JSX 标题标签，若各 locale 副本均已如此，请从 JSX_HEADING_EXEMPT_DOCS 摘除 ${docPath}`,
        );
      }
    }
  }

  for (const docPath of JSX_HEADING_EXEMPT_DOCS) {
    if (!exemptDocsFound.has(docPath)) {
      problems.push(`${docPath} 登记在 JSX_HEADING_EXEMPT_DOCS 中但源与各 locale 译文均无此文件，登记项已过期，请摘除`);
    }
  }

  return problems;
}

// ---- 4. Complete UI JSON keys (compare against write-translations output) ----
//
// footer.json `copyright` freezes the year at write-translations execution in English translations,
// while site configuration evaluates copyright dynamically for the current year, causing annual drift. Intentionally omit that key so rendering
// falls back to the dynamically evaluated source-language value; exclude it from completeness validation.
const UI_JSON_FILES = [
  "i18n/en/code.json",
  "i18n/en/docusaurus-theme-classic/navbar.json",
  "i18n/en/docusaurus-theme-classic/footer.json",
  "i18n/en/docusaurus-plugin-content-docs/current.json",
];
const KNOWN_OMITTED_KEYS = new Map([["i18n/en/docusaurus-theme-classic/footer.json", new Set(["copyright"])]]);

function readKeys(relativePath) {
  const path = resolve(websiteDir, relativePath);
  if (!existsSync(path)) return new Set();
  return new Set(Object.keys(JSON.parse(readFileSync(path, "utf8"))));
}

function checkUiJsonKeys() {
  const problems = [];
  const before = new Map(UI_JSON_FILES.map((file) => [file, readKeys(file)]));
  // write-translations rewrites delegated files in place. Restore byte-for-byte from in-memory snapshots rather than git checkout:
  // checkout would also discard uncommitted translation edits (local callers just edited UI JSON),
  // and cannot remove files newly created by write-translations that did not exist before.
  const snapshots = new Map(
    UI_JSON_FILES.map((file) => {
      const path = resolve(websiteDir, file);
      return [file, existsSync(path) ? readFileSync(path) : null];
    }),
  );

  try {
    execFileSync("pnpm", ["exec", "docusaurus", "write-translations", "--locale", "en"], {
      cwd: websiteDir,
      stdio: "pipe",
    });
    for (const file of UI_JSON_FILES) {
      const beforeKeys = before.get(file);
      const omitted = KNOWN_OMITTED_KEYS.get(file) ?? new Set();
      const missing = [...readKeys(file)].filter((key) => !beforeKeys.has(key) && !omitted.has(key));
      if (missing.length > 0) {
        problems.push(`${file} 缺少 write-translations 生成的 key：${missing.join(", ")}`);
      }
    }
  } finally {
    for (const [file, content] of snapshots) {
      const path = resolve(websiteDir, file);
      if (content === null) rmSync(path, { force: true });
      else writeFileSync(path, content);
    }
  }

  return problems;
}

const problems = [...checkDocInventory(), ...checkOrphanTranslations(), ...checkAnchors(), ...checkUiJsonKeys()];

if (problems.length > 0) {
  console.error("一致性检查未通过：");
  for (const problem of problems) console.error(`  - ${problem}`);
  process.exitCode = 1;
} else {
  console.log("一致性检查通过：文档库存一致、无孤儿译文、标题锚点齐全且唯一、UI JSON key 齐全。");
}
