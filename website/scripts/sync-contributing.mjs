// 把仓库根的 CONTRIBUTING.md 复制成文档站的开发区页面。
// 真相源留在仓库根，副本是构建产物（website/.gitignore 忽略），build / start 前置执行。
// pnpm 10 默认不跑 pre/post script，所以由 package.json 的 build / start / sync-contributing 显式串联。

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

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
  ["## 运行测试", "running-tests"],
  ["## 代码质量", "code-quality"],
  ["### ESLint disable 使用规范", "eslint-disable-policy"],
  ["### Pytest markers 纪律", "pytest-markers"],
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

const FENCE = /^\s*(```|~~~)/;
const HEADING = /^(#{1,6})\s+(.*?)\s*$/;

function injectAnchors(markdown) {
  const seen = new Set();
  const usedAnchors = new Set();
  let fence = "";
  const lines = markdown.split("\n").map((line) => {
    const fenceMatch = FENCE.exec(line);
    if (fenceMatch) {
      if (!fence) {
        fence = fenceMatch[1];
      } else if (line.trim().startsWith(fence)) {
        fence = "";
      }
      return line;
    }
    if (fence) return line;

    const heading = HEADING.exec(line);
    if (!heading) return line;

    const [, hashes, text] = heading;
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
    return `${hashes} ${text} {#${anchor}}`;
  });

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
