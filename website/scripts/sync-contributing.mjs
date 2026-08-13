// 把仓库根的 CONTRIBUTING.md 复制成文档站的开发区页面。
// 真相源留在仓库根，副本是构建产物（website/.gitignore 忽略），build / start 前置执行。
// pnpm 10 默认不跑 pre/post script，所以由 package.json 的 build / start / sync-contributing 显式串联。

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const websiteDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = resolve(websiteDir, "..", "CONTRIBUTING.md");
const target = resolve(websiteDir, "docs", "dev", "contributing.md");

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

const body = await readFile(source, "utf8");
await mkdir(dirname(target), { recursive: true });
await writeFile(target, frontmatter + body, "utf8");
