#!/usr/bin/env node

import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

// 与页面库存、锚点扫描共用同一个遍历器，三道 CI 闸门对「哪些文件算文档」给同一答案。
// 该模块零 npm 依赖，本脚本仍可用 node 直接运行；代价是耦合仓库文件布局——本脚本
// 本就硬编码 website/i18n 等仓库路径，不追求脱库复用。
import { walkMarkdownFiles } from "../../../../website/scripts/markdown-scan.mjs";

const I18N_ROOT = "website/i18n";
const DOCS_TRANSLATION_SUBDIRECTORY = "docusaurus-plugin-content-docs/current";
const DOCS_TRANSLATION_ROOT = `${I18N_ROOT}/en/${DOCS_TRANSLATION_SUBDIRECTORY}`;
const LOCK_PATH = "website/i18n/translation.lock.json";

// Forward mapping registers English targets only, while the orphan scan walks every locale
// directory under website/i18n/. Extend this mapping before adding another documentation
// locale — otherwise every file of the new locale is reported as an unregistered orphan
// and `record` refuses to run.
function targetForSource(source) {
  if (source === "CONTRIBUTING.md") return `${DOCS_TRANSLATION_ROOT}/dev/contributing.md`;
  if (source === "README.md") return "README.en.md";
  if (source.startsWith("website/docs/")) {
    return `${DOCS_TRANSLATION_ROOT}/${source.slice("website/docs/".length)}`;
  }
  return null;
}

// CONTRIBUTING.md is translated from its synced copy, not its own bytes, so its fingerprint
// must track that copy: a sync-contributing.mjs change alone (frontmatter, anchors) makes the
// translated target stale even when CONTRIBUTING.md itself hasn't changed.
function fingerprintSource(source) {
  if (source === "CONTRIBUTING.md") return "website/docs/dev/contributing.md";
  return source;
}

function sourceTargets(root) {
  const mappings = [
    ["CONTRIBUTING.md", targetForSource("CONTRIBUTING.md")],
    ["README.md", targetForSource("README.md")],
    ...walkMarkdownFiles(root, "website/docs")
      .filter((source) => source !== "website/docs/dev/contributing.md")
      .map((source) => [source, targetForSource(source)]),
  ];
  return mappings
    .filter(([source]) => existsSync(resolve(root, source)))
    .sort(([left], [right]) => left.localeCompare(right));
}

function documentTranslationTargets(root) {
  const absoluteI18nRoot = resolve(root, I18N_ROOT);
  if (!existsSync(absoluteI18nRoot)) return [];

  return readdirSync(absoluteI18nRoot, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .flatMap((entry) => walkMarkdownFiles(root, `${I18N_ROOT}/${entry.name}/${DOCS_TRANSLATION_SUBDIRECTORY}`))
    .sort((left, right) => left.localeCompare(right));
}

function sourceForTranslationTarget(target) {
  const marker = `/${DOCS_TRANSLATION_SUBDIRECTORY}/`;
  const relativeTarget = target.slice(target.indexOf(marker) + marker.length);
  if (relativeTarget === "dev/contributing.md") return "CONTRIBUTING.md";
  return `website/docs/${relativeTarget}`;
}

// A target is registered when a current source maps to it (its lock entry may still be missing or
// stale) or when a lock entry maps to it (the forward scan already reports it as an orphan).
function unregisteredTranslationOrphans(root, lock, currentTargets) {
  const registeredTargets = new Set([
    ...currentTargets,
    ...Object.keys(lock).map(targetForSource).filter((target) => target !== null),
  ]);
  return documentTranslationTargets(root)
    .filter((target) => !registeredTargets.has(target))
    .map((target) => ({ source: sourceForTranslationTarget(target), target, state: "orphan" }));
}

function digest(path) {
  const normalized = readFileSync(path, "utf8").replace(/\r\n?/g, "\n");
  return createHash("sha256").update(normalized, "utf8").digest("hex");
}

function readLock(root) {
  const path = resolve(root, LOCK_PATH);
  if (!existsSync(path)) return {};
  return JSON.parse(readFileSync(path, "utf8"));
}

function translationStatus(root) {
  const lock = readLock(root);
  const mappings = sourceTargets(root);
  const currentSources = new Set(mappings.map(([source]) => source));
  const currentTargets = new Set(mappings.map(([, target]) => target));
  const dirty = mappings.flatMap(([source, target]) => {
    if (!existsSync(resolve(root, target))) return [{ source, target, state: "missing" }];
    if (lock[source] !== digest(resolve(root, fingerprintSource(source)))) return [{ source, target, state: "stale" }];
    return [];
  });
  const orphans = Object.keys(lock)
    .filter((source) => !currentSources.has(source))
    .map((source) => ({ source, target: targetForSource(source), state: "orphan" }));
  return [...dirty, ...orphans, ...unregisteredTranslationOrphans(root, lock, currentTargets)].sort(
    (left, right) => left.source.localeCompare(right.source) || left.target.localeCompare(right.target),
  );
}

function recordTranslations(root) {
  const mappings = sourceTargets(root);
  const missing = mappings.filter(([, target]) => !existsSync(resolve(root, target)));
  if (missing.length > 0) {
    throw new Error(`Refusing to record missing translations:\n${missing.map(([source]) => source).join("\n")}`);
  }
  const currentSources = new Set(mappings.map(([source]) => source));
  const currentTargets = new Set(mappings.map(([, target]) => target));
  const orphanTargets = [
    ...Object.keys(readLock(root))
      .filter((source) => !currentSources.has(source))
      .map(targetForSource)
      .filter((target) => target !== null && existsSync(resolve(root, target))),
    ...documentTranslationTargets(root).filter((target) => !currentTargets.has(target)),
  ];
  if (orphanTargets.length > 0) {
    throw new Error(`Refusing to record orphan translations:\n${[...new Set(orphanTargets)].sort().join("\n")}`);
  }

  const lock = Object.fromEntries(
    mappings.map(([source]) => [source, digest(resolve(root, fingerprintSource(source)))]),
  );
  const lockPath = resolve(root, LOCK_PATH);
  const temporaryPath = `${lockPath}.${process.pid}.tmp`;
  mkdirSync(dirname(lockPath), { recursive: true });
  writeFileSync(temporaryPath, `${JSON.stringify(lock, null, 2)}\n`, "utf8");
  renameSync(temporaryPath, lockPath);
  return mappings.length;
}

function parseArguments(argv) {
  const command = argv[0];
  const rootIndex = argv.indexOf("--root");
  return {
    command,
    root: rootIndex === -1 ? process.cwd() : resolve(argv[rootIndex + 1]),
    json: argv.includes("--json"),
  };
}

const { command, root, json } = parseArguments(process.argv.slice(2));

if (command !== "status" && command !== "record") {
  console.error("Usage: translation-lock.mjs <status|record> [--root PATH] [--json]");
  process.exitCode = 2;
} else if (command === "record") {
  try {
    console.log(`Recorded ${recordTranslations(root)} source hashes.`);
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
} else {
  const status = translationStatus(root);
  if (json) {
    process.stdout.write(`${JSON.stringify(status, null, 2)}\n`);
  } else if (status.length === 0) {
    console.log("Translations are up to date.");
  } else {
    for (const item of status) console.log(`${item.state}\t${item.source}\t${item.target}`);
  }
}
