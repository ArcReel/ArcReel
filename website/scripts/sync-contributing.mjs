// Copy root CONTRIBUTING.md into the documentation-site developer section.
// The source of truth remains at repository root; the copy is a build artifact (ignored by website/.gitignore), generated before build / start.
// pnpm 10 does not run pre/post scripts by default, so package.json build / start / sync-contributing explicitly chain this step.

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { scanMarkdownLines } from "./markdown-scan.mjs";

const websiteDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = resolve(websiteDir, "..", "CONTRIBUTING.md");
const target = resolve(websiteDir, "docs", "dev", "contributing.md");

// Inject explicit anchor IDs during copying, leaving root source untouched: GitHub does not recognize `{#id}` and would display it as text.
// Register heading changes here too or copying fails: anchors are shared link targets for Chinese and English locales and must not drift silently.
// Keys combine hash prefixes and heading text, allowing equal titles (e.g. two “工作流程” headings) at different levels to register separately.
const ANCHORS = new Map([
  ["# 贡献指南", "contributing"],
  ["## 本地开发环境", "local-development"],
  ["### 文档站", "docs-site"],
  ["## 测试", "testing"],
  ["### 前端页面级测试", "frontend-page-tests"],
  ["## 代码质量", "code-quality"],
  ["### 依赖管理", "dependency-management"],
  ["## 文档维护", "docs-maintenance"],
  ["### 各页职责", "page-responsibilities"],
  ["### 写作约定", "writing-conventions"],
  ["## 工作流程", "workflow"],
  ["### 分支策略（trunk-based）", "branching-strategy"],
  ["### 分支命名约定", "branch-naming"],
  ["### 短分支寿命", "short-lived-branches"],
  ["### Squash merge", "squash-merge"],
  ["## 提交规范", "commit-convention"],
  ["## 发版流程", "release-process"],
  ["### 工作流程", "release-workflow"],
  ["### commit type → 版本步进", "commit-type-version-bump"],
  ["### commit 示例", "commit-examples"],
]);

function injectAnchors(markdown) {
  const seen = new Set();
  const usedAnchors = new Set();
  const lines = markdown.split("\n");

  for (const { index, hashes, text } of scanMarkdownLines(markdown)) {
    if (hashes === null) continue;

    const key = `${hashes} ${text}`;
    const anchor = ANCHORS.get(key);
    if (!anchor) {
      throw new Error(
        `CONTRIBUTING.md 的标题「${key}」没有登记锚点 ID，请在 website/scripts/sync-contributing.mjs 的 ANCHORS 中补上`,
      );
    }
    // Duplicate IDs create invalid HTML and anchor links reach only the first occurrence; onBrokenAnchors cannot detect this collision.
    if (usedAnchors.has(anchor)) {
      throw new Error(`锚点 ID「${anchor}」被多个标题共用（最后一处是「${key}」），请在 ANCHORS 中改成唯一值`);
    }
    usedAnchors.add(anchor);
    seen.add(key);
    lines[index] = `${lines[index].replace(/\s*$/, "")} {#${anchor}}`;
  }

  const stale = [...ANCHORS.keys()].filter((key) => !seen.has(key));
  if (stale.length > 0) {
    throw new Error(`ANCHORS 中登记了 CONTRIBUTING.md 里已不存在的标题：${stale.join("、")}`);
  }
  return lines.join("\n");
}

// The copy is untracked; “Edit this page” must point to root source, or it targets nonexistent website/docs/dev/contributing.md.
const frontmatter = [
  "---",
  "id: contributing",
  "title: 贡献指南",
  "sidebar_position: 2",
  "custom_edit_url: https://github.com/ArcReel/ArcReel/blob/main/CONTRIBUTING.md",
  "---",
  "",
  "",
].join("\n");

// With Git autocrlf on Windows, CONTRIBUTING.md checks out as CRLF; split("\n") leaves trailing
// \r on non-heading lines but replacements drop it on headings, producing mixed line endings that fail Prettier etc. Normalize to \n after reading.
const body = injectAnchors((await readFile(source, "utf8")).replace(/\r\n/g, "\n"));
await mkdir(dirname(target), { recursive: true });
await writeFile(target, frontmatter + body, "utf8");
