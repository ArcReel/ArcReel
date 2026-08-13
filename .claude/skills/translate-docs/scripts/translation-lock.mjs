#!/usr/bin/env node

import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, relative, resolve, sep } from "node:path";

const DOCS_TRANSLATION_ROOT = "website/i18n/en/docusaurus-plugin-content-docs/current";
const LOCK_PATH = "website/i18n/translation.lock.json";

function toPosix(path) {
  return path.split(sep).join("/");
}

function walkMarkdown(root, directory) {
  const absoluteDirectory = resolve(root, directory);
  if (!existsSync(absoluteDirectory)) return [];

  return readdirSync(absoluteDirectory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(absoluteDirectory, entry.name);
    if (entry.isDirectory()) return walkMarkdown(root, toPosix(relative(root, path)));
    if (!entry.isFile() || !/\.mdx?$/.test(entry.name)) return [];
    return [toPosix(relative(root, path))];
  });
}

function targetForSource(source) {
  if (source === "CONTRIBUTING.md") return `${DOCS_TRANSLATION_ROOT}/dev/contributing.md`;
  if (source === "README.md") return "README.en.md";
  if (source.startsWith("website/docs/")) {
    return `${DOCS_TRANSLATION_ROOT}/${source.slice("website/docs/".length)}`;
  }
  return null;
}

function sourceTargets(root) {
  const mappings = [
    ["CONTRIBUTING.md", targetForSource("CONTRIBUTING.md")],
    ["README.md", targetForSource("README.md")],
    ...walkMarkdown(root, "website/docs")
      .filter((source) => source !== "website/docs/dev/contributing.md")
      .map((source) => [source, targetForSource(source)]),
  ];
  return mappings
    .filter(([source]) => existsSync(resolve(root, source)))
    .sort(([left], [right]) => left.localeCompare(right));
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
  const dirty = mappings.flatMap(([source, target]) => {
    if (!existsSync(resolve(root, target))) return [{ source, target, state: "missing" }];
    if (lock[source] !== digest(resolve(root, source))) return [{ source, target, state: "stale" }];
    return [];
  });
  const orphans = Object.keys(lock)
    .filter((source) => !currentSources.has(source))
    .map((source) => ({ source, target: targetForSource(source), state: "orphan" }));
  return [...dirty, ...orphans].sort((left, right) => left.source.localeCompare(right.source));
}

function recordTranslations(root) {
  const mappings = sourceTargets(root);
  const missing = mappings.filter(([, target]) => !existsSync(resolve(root, target)));
  if (missing.length > 0) {
    throw new Error(`Refusing to record missing translations:\n${missing.map(([source]) => source).join("\n")}`);
  }
  const currentSources = new Set(mappings.map(([source]) => source));
  const orphanTargets = Object.keys(readLock(root))
    .filter((source) => !currentSources.has(source))
    .map(targetForSource)
    .filter((target) => target !== null && existsSync(resolve(root, target)));
  if (orphanTargets.length > 0) {
    throw new Error(`Refusing to record orphan translations:\n${orphanTargets.join("\n")}`);
  }

  const lock = Object.fromEntries(mappings.map(([source]) => [source, digest(resolve(root, source))]));
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
