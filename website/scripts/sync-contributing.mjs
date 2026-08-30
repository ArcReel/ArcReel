// 把仓库根的 CONTRIBUTING.md 复制成文档站的开发区页面。
// 真相源留在仓库根，副本是构建产物（website/.gitignore 忽略），build / start 前置执行。
// pnpm 10 默认不跑 pre/post script，所以由 package.json 的 build / start / sync-contributing 显式串联。

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { scanMarkdownLines } from "./markdown-scan.mjs";

const websiteDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = resolve(websiteDir, "..", "CONTRIBUTING.md");
const target = resolve(websiteDir, "docs", "dev", "contributing.md");

// 显式锚点 ID 在复制时注入，不写进仓库根的真相源：GitHub 不认 `{#id}` 语法，会把它当正文原样显示。
// 标题改动后这里必须同步登记，否则复制失败——锚点是中英两个 locale 共用的链接目标，不能静默漂移。
// 键是「井号前缀 + 标题文本」：同名标题（如两处「工作流程」）出现在不同层级时才能各自登记。
const ANCHORS = new Map([
  ["# 贡献指南", "contributing"],
  ["## 本地开发环境", "local-development"],
  ["### 文档站", "docs-site"],
  ["## 测试", "testing"],
  ["### 测试选择", "test-selection"],
  ["### 分层与目录", "test-tiers"],
  ["### 体量与命名", "test-file-size"],
  ["### 测试替身", "test-doubles"],
  ["### 无意义测试判据", "meaningless-tests"],
  ["### 共享设施", "shared-test-fixtures"],
  ["### 时序与偶发失败", "timing-and-flakiness"],
  ["### 覆盖率", "coverage"],
  ["### 闸门", "test-gates"],
  ["### 前端测试（vitest）", "frontend-vitest"],
  ["## 代码质量", "code-quality"],
  ["### 依赖管理", "dependency-management"],
  ["### 注释规范", "comment-discipline"],
  ["### ESLint disable 使用规范", "eslint-disable-policy"],
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
    // 重复 id 会产出无效 HTML，且锚点链接只会落到第一处；onBrokenAnchors 查不出这种碰撞。
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

// 副本不入库，「编辑此页」须指回仓库根的真相源，否则会指向不存在的 website/docs/dev/contributing.md。
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
