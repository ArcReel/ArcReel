#!/usr/bin/env node
// CI 一致性闸门：update-docs 页面库存 / 孤儿译文 / 上站文档标题显式锚点 / UI JSON key 齐全性。
// 任一命中即非零退出；缺译/滞后清单不在本脚本范围（translation-lock.mjs status 已覆盖，
// 由 workflow 单独一步写入 step summary，不阻断构建）。

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

// ---- 1. update-docs 页面库存与 CONTRIBUTING 各页职责清单 ----

function checkDocInventory() {
  return checkUpdateDocsInventory(repoRoot).problems;
}

// ---- 2. 孤儿译文：委托给翻译 skill 的唯一真相源（.claude/skills/translate-docs/），不重复实现。 ----

function checkOrphanTranslations() {
  const lockScript = resolve(repoRoot, ".claude/skills/translate-docs/scripts/translation-lock.mjs");
  const output = execFileSync(process.execPath, [lockScript, "status", "--root", repoRoot, "--json"], {
    encoding: "utf8",
  });
  const orphans = JSON.parse(output).filter((item) => item.state === "orphan");
  // 孤儿有两种来源：lockfile 里源已删的登记项，以及反向扫描发现的未登记译文文件。
  // 后者的 source 是按目标路径反推的、未必存在的源，所以措辞不断言「已不存在」。
  return orphans.map((item) => `孤儿译文：${item.target}（源 ${item.source} 未登记或已不存在）`);
}

// ---- 3. 上站文档标题缺显式锚点 ----
//
// 全部标题须带 `{#id}`（各 locale 共用锚点作为链接目标）。译文与源同结构、受同一套规则约束：
// 只扫源的话，译文漏写 `{#id}` 只有在恰好有链接指向该锚点时才会被 build 期 onBrokenAnchors 拦下。
//
// index.mdx 的首页卡片标题是 JSX `<h2>`，`{#id}` 语法在 JSX 里不生效，无法补锚点。这里显式登记
// 豁免文件，而不是让扫描器对 JSX 标题沉默：新增 .mdx 若引入未登记的 JSX 标题会在此处 fail，逼
// 审查者显式决定豁免还是改回 Markdown 标题；已登记文件若不再含 JSX 标题也会 fail（登记项过期，
// 须及时摘除）。登记路径相对文档根，源与各 locale 译文里的同名副本一并豁免。
const JSX_HEADING_EXEMPT_DOCS = new Set(["index.mdx"]);

const ANCHOR_SUFFIX = /\{#([a-z0-9-]+)\}$/;

const TRANSLATION_DOCS_SUFFIX = "docusaurus-plugin-content-docs/current";

// 文档根：中文源目录，加 i18n 下每个 locale 的译文目录。新增 locale 自动纳入，无需登记。
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

// ---- 4. UI JSON key 齐全性（比照 write-translations 输出比对） ----
//
// footer.json 的 `copyright` 键会把 write-translations 运行那一刻的年份写死进英文译文，
// 而站点配置按当前年份动态求值版权文案——两者逐年错开。故意不提交该键，让其在渲染期
// 回退到源语言的动态求值，不计入完整性校验。
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
  // write-translations 会就地改写委托文件。恢复用内存快照按字节写回，而不是 git checkout：
  // 后者会连同工作区里尚未提交的译文编辑一起抹掉（本地跑这个检查的正是刚编辑完 UI JSON 的人），
  // 且原本不存在、被 write-translations 新建的文件也无法靠 checkout 清除。
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
