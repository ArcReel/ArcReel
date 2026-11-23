import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { scanMarkdownLines } from "./markdown-scan.mjs";

const websiteDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = resolve(websiteDir, "..", "CONTRIBUTING.md");
const target = resolve(websiteDir, "docs", "dev", "contributing.md");

// 显式锚点 ID 在复制时注入，不写进仓库根的真相源：GitHub 不认 `{#id}` 语法，会把它当正文原样显示。
// 标题改动后这里必须同步登记，否则复制失败——锚点是中英两个 locale 共用的链接目标，不能静默漂移。
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

// Windows 下 Git 若开启 autocrlf，CONTRIBUTING.md 会以 CRLF 检出；split("\n") 按行切分后非标题行仍带尾部
// \r、标题行被替换后丢失 \r，混合换行符写入产物会触发 Prettier 等格式检查。读取后统一归一为 \n。
const body = injectAnchors((await readFile(source, "utf8")).replace(/\r\n/g, "\n"));
await mkdir(dirname(target), { recursive: true });
await writeFile(target, frontmatter + body, "utf8");
