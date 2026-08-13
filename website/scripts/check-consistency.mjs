#!/usr/bin/env node
// CI 一致性闸门：孤儿译文 / 上站文档标题缺显式锚点 / UI JSON key 齐全性。
// 三项任一命中即非零退出；缺译/滞后清单不在本脚本范围（translation-lock.mjs status 已覆盖，
// 由 workflow 单独一步写入 step summary，不阻断构建）。

import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const websiteDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(websiteDir, "..");

function toPosix(path) {
  return path.split(sep).join("/");
}

// ---- 1. 孤儿译文：委托给翻译 skill 的唯一真相源（.claude/skills/translate-docs/），不重复实现。 ----

function checkOrphanTranslations() {
  const lockScript = resolve(repoRoot, ".claude/skills/translate-docs/scripts/translation-lock.mjs");
  const output = execFileSync(process.execPath, [lockScript, "status", "--root", repoRoot, "--json"], {
    encoding: "utf8",
  });
  const orphans = JSON.parse(output).filter((item) => item.state === "orphan");
  return orphans.map((item) => `孤儿译文：${item.target}（源 ${item.source} 已不存在）`);
}

// ---- 2. 上站文档标题缺显式锚点 ----
//
// 全部标题须带 `{#id}`（两 locale 共用锚点作为链接目标）。index.mdx 的首页卡片标题是 JSX
// `<h2>`，`{#id}` 语法在 JSX 里不生效，无法补锚点。这里显式登记豁免文件，而不是让扫描器
// 对 JSX 标题沉默：新增 .mdx 若引入未登记的 JSX 标题会在此处 fail，逼审查者显式决定豁免
// 还是改回 Markdown 标题；已登记文件若不再含 JSX 标题也会 fail（登记项过期，须及时摘除）。
const JSX_HEADING_EXEMPT_FILES = new Set(["docs/index.mdx"]);

const FENCE = /^\s*(```|~~~)/;
const HEADING = /^ {0,3}(#{1,6})\s+(.*?)\s*$/;
const ANCHOR_SUFFIX = /\{#([a-z0-9-]+)\}$/;
const JSX_HEADING = /<h[1-6][\s/>]/i;

function walkDocFiles(directory) {
  const absoluteDirectory = resolve(websiteDir, directory);
  if (!existsSync(absoluteDirectory)) return [];
  return readdirSync(absoluteDirectory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(absoluteDirectory, entry.name);
    if (entry.isDirectory()) return walkDocFiles(toPosix(relative(websiteDir, path)));
    if (!entry.isFile() || !/\.mdx?$/.test(entry.name)) return [];
    return [toPosix(relative(websiteDir, path))];
  });
}

function checkAnchors() {
  const problems = [];
  const jsxHeadingFiles = new Set();

  for (const file of walkDocFiles("docs")) {
    const content = readFileSync(resolve(websiteDir, file), "utf8");
    const lines = content.split("\n");
    const seenAnchors = new Set();
    let fence = "";

    for (const [index, line] of lines.entries()) {
      const fenceMatch = FENCE.exec(line);
      if (fenceMatch) {
        if (!fence) fence = fenceMatch[1];
        else if (line.trim().startsWith(fence)) fence = "";
        continue;
      }
      if (fence) continue;

      if (JSX_HEADING.test(line)) jsxHeadingFiles.add(file);

      const heading = HEADING.exec(line);
      if (!heading) continue;
      const [, , text] = heading;
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

  for (const file of jsxHeadingFiles) {
    if (!JSX_HEADING_EXEMPT_FILES.has(file)) {
      problems.push(
        `${file} 含 JSX 标题标签但未登记在 check-consistency.mjs 的 JSX_HEADING_EXEMPT_FILES 中——` +
          "要么改回带 {#id} 的 Markdown 标题，要么显式登记豁免",
      );
    }
  }
  for (const file of JSX_HEADING_EXEMPT_FILES) {
    if (!jsxHeadingFiles.has(file)) {
      problems.push(`${file} 登记在 JSX_HEADING_EXEMPT_FILES 中但已不含 JSX 标题标签，登记项已过期，请摘除`);
    }
  }

  return problems;
}

// ---- 3. UI JSON key 齐全性（比照 write-translations 输出比对） ----
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

const problems = [...checkOrphanTranslations(), ...checkAnchors(), ...checkUiJsonKeys()];

if (problems.length > 0) {
  console.error("一致性检查未通过：");
  for (const problem of problems) console.error(`  - ${problem}`);
  process.exitCode = 1;
} else {
  console.log("一致性检查通过：无孤儿译文、标题锚点齐全且唯一、UI JSON key 齐全。");
}
