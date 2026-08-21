#!/usr/bin/env node
// update-docs 的上站页面库存：frontmatter 声明覆盖档位，CONTRIBUTING「各页职责」必须与库存一致。

import { existsSync, readdirSync, readFileSync } from "node:fs";
import { relative, resolve, sep } from "node:path";
import { pathToFileURL } from "node:url";

import { readFrontMatterScalar, scanMarkdownLines } from "./markdown-scan.mjs";

const UPDATE_DOCS_VALUES = new Set(["full", "fact-check", "none"]);
// CONTRIBUTING 的中文副本由 sync-contributing 在构建期生成，真相源不在 website/docs，不能进入库存。
const GENERATED_DOCS = new Set(["website/docs/dev/contributing.md"]);

function toPosix(path) {
  return path.split(sep).join("/");
}

function walkMarkdownFiles(directory) {
  if (!existsSync(directory)) return [];
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) return walkMarkdownFiles(path);
    if (!entry.isFile() || !/\.mdx?$/.test(entry.name)) return [];
    return [path];
  });
}

function responsibilityDocPaths(content) {
  const scannedLines = [...scanMarkdownLines(content)];
  let sectionStart = null;
  let sectionEnd = Number.POSITIVE_INFINITY;

  for (const { index, hashes, text } of scannedLines) {
    if (hashes === null) continue;
    if (sectionStart === null && hashes === "###" && text === "各页职责") {
      sectionStart = index + 1;
      continue;
    }
    if (sectionStart !== null && hashes.length <= 3) {
      sectionEnd = index;
      break;
    }
  }
  if (sectionStart === null) return null;

  const paths = [];
  for (const { index, line } of scannedLines) {
    if (index < sectionStart || index >= sectionEnd) continue;
    const match = /^\|\s*`(website\/docs\/[^`]+\.mdx?)`\s*\|/.exec(line);
    if (match) paths.push(match[1]);
  }
  return paths;
}

/**
 * @param {string} repoRoot
 * @returns {{ entries: Array<{ path: string, updateDocs: string }>, problems: string[] }}
 */
export function checkUpdateDocsInventory(repoRoot) {
  const docsDir = resolve(repoRoot, "website/docs");
  const entries = walkMarkdownFiles(docsDir)
    .map((path) => ({
      path: toPosix(relative(repoRoot, path)),
      updateDocs: readFrontMatterScalar(readFileSync(path, "utf8"), "update_docs"),
    }))
    .filter((entry) => !GENERATED_DOCS.has(entry.path))
    // 按 POSIX 相对路径排序：目录分隔符已归一，清单顺序在各平台一致。
    .sort((left, right) => (left.path < right.path ? -1 : left.path > right.path ? 1 : 0));
  const problems = [];

  for (const entry of entries) {
    if (entry.updateDocs === null) {
      problems.push(`${entry.path} 未声明 frontmatter 的 update_docs（可选 full / fact-check / none）`);
    } else if (!UPDATE_DOCS_VALUES.has(entry.updateDocs)) {
      problems.push(
        `${entry.path} 的 frontmatter update_docs 值「${entry.updateDocs}」无效（可选 full / fact-check / none）`,
      );
    }
  }

  const contributingPath = resolve(repoRoot, "CONTRIBUTING.md");
  const responsibilityPaths = existsSync(contributingPath)
    ? responsibilityDocPaths(readFileSync(contributingPath, "utf8"))
    : null;
  if (responsibilityPaths === null) {
    problems.push("CONTRIBUTING.md 缺少「### 各页职责」章节");
  } else {
    const actual = new Set(entries.map((entry) => entry.path));
    const declared = new Set(responsibilityPaths);
    const missing = [...actual].filter((path) => !declared.has(path));
    const stale = [...declared].filter((path) => !actual.has(path));
    if (missing.length > 0) problems.push(`CONTRIBUTING.md 各页职责缺少：${missing.join("、")}`);
    if (stale.length > 0) problems.push(`CONTRIBUTING.md 各页职责多出：${stale.join("、")}`);
  }

  return {
    entries: entries.map((entry) => ({ path: entry.path, updateDocs: entry.updateDocs ?? "" })),
    problems,
  };
}

function parseArgs(args) {
  let root = resolve(import.meta.dirname, "../..");
  let format = "check";
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === "--root" && args[index + 1]) root = resolve(args[++index]);
    else if (args[index] === "--format" && args[index + 1]) format = args[++index];
    else throw new Error(`未知参数：${args[index]}`);
  }
  if (format !== "check" && format !== "tsv") throw new Error(`未知输出格式：${format}`);
  return { root, format };
}

function main() {
  const { root, format } = parseArgs(process.argv.slice(2));
  const { entries, problems } = checkUpdateDocsInventory(root);
  if (problems.length > 0) {
    console.error("update-docs 文档库存检查未通过：");
    for (const problem of problems) console.error(`  - ${problem}`);
    process.exitCode = 1;
    return;
  }
  if (format === "tsv") {
    for (const entry of entries) console.log(`${entry.updateDocs}\t${entry.path}`);
  } else {
    console.log(`update-docs 文档库存检查通过：${entries.length} 个上站页面均已声明覆盖档位且各页职责清单一致。`);
  }
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) main();
