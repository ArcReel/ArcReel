#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

import { scanMarkdownLines, walkMarkdownFiles } from "./markdown-scan.mjs";

const I18N_ROOT = "website/i18n";
const DOCS_TRANSLATION_SUBDIRECTORY = "docusaurus-plugin-content-docs/current";
const DOCS_TRANSLATION_ROOT = `${I18N_ROOT}/en/${DOCS_TRANSLATION_SUBDIRECTORY}`;
const LOCK_PATH = "website/i18n/translation.lock.json";

function targetForSource(source) {
  if (source === "CONTRIBUTING.md") return `${DOCS_TRANSLATION_ROOT}/dev/contributing.md`;
  if (source === "README.md") return "README.en.md";
  if (source.startsWith("website/docs/")) {
    return `${DOCS_TRANSLATION_ROOT}/${source.slice("website/docs/".length)}`;
  }
  return null;
}

function fingerprintSource(source) {
  if (source === "CONTRIBUTING.md") return "website/docs/dev/contributing.md";
  return source;
}

function syncContributing(root) {
  const translated = existsSync(resolve(root, targetForSource("CONTRIBUTING.md")));
  if (!translated || !existsSync(resolve(root, "CONTRIBUTING.md"))) return;
  const result = spawnSync(process.execPath, [resolve(root, "website/scripts/sync-contributing.mjs")], {
    cwd: root,
    encoding: "utf8",
  });
  if (result.status !== 0) {
    throw new Error(
      `Could not generate ${fingerprintSource("CONTRIBUTING.md")} with \`pnpm sync-contributing\` in website/.\n${result.stderr ?? ""}`,
    );
  }
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

function unregisteredTranslationOrphans(root, lock, currentTargets) {
  const registeredTargets = new Set([
    ...currentTargets,
    ...Object.keys(lock)
      .map(targetForSource)
      .filter((target) => target !== null),
  ]);
  return documentTranslationTargets(root)
    .filter((target) => !registeredTargets.has(target))
    .map((target) => ({ source: sourceForTranslationTarget(target), target, state: "orphan" }));
}

const TRANSLATED_FRONT_MATTER_KEYS = new Set(["title", "description", "sidebar_label"]);

function untranslatedFrontMatter(path) {
  const lines = readFileSync(path, "utf8")
    .replace(/^\uFEFF/, "")
    .replace(/\r\n?/g, "\n")
    .split("\n");
  if (lines[0]?.trim() !== "---") return [];
  const entries = [];
  for (const line of lines.slice(1)) {
    if (line.trim() === "---") break;
    if (/^\S/.test(line) || entries.length === 0) {
      const key = /^(["']?)([^"':\s]+)\1\s*:/.exec(line)?.[2] ?? null;
      entries.push({ key, text: line });
    } else {
      entries.at(-1).text += `\n${line}`;
    }
  }
  return entries.filter(({ key }) => !TRANSLATED_FRONT_MATTER_KEYS.has(key)).map(({ text }) => text);
}

function frontMatterDrift(root, mappings) {
  return mappings.filter(([source, target]) => {
    const targetPath = resolve(root, target);
    if (!existsSync(targetPath)) return false;
    const expected = untranslatedFrontMatter(resolve(root, fingerprintSource(source)));
    return JSON.stringify(expected) !== JSON.stringify(untranslatedFrontMatter(targetPath));
  });
}

function sectionAnchors(path) {
  return [...scanMarkdownLines(readFileSync(path, "utf8"))].flatMap(({ hashes, anchor }) =>
    anchor === null ? [] : [`${hashes} ${anchor}`],
  );
}

function sectionAnchorDrift(root, mappings) {
  return mappings.filter(([source, target]) => {
    if (source === "README.md" || !existsSync(resolve(root, target))) return false;
    const expected = sectionAnchors(resolve(root, fingerprintSource(source)));
    return JSON.stringify(expected) !== JSON.stringify(sectionAnchors(resolve(root, target)));
  });
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

export function translationStatus(root) {
  syncContributing(root);
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
  const drifted = frontMatterDrift(root, mappings).map(([source, target]) => ({
    source,
    target,
    state: "frontmatter",
  }));
  const anchors = sectionAnchorDrift(root, mappings).map(([source, target]) => ({ source, target, state: "anchors" }));
  return [
    ...dirty,
    ...drifted,
    ...anchors,
    ...orphans,
    ...unregisteredTranslationOrphans(root, lock, currentTargets),
  ].sort(
    (left, right) =>
      left.source.localeCompare(right.source) ||
      (left.target ?? "").localeCompare(right.target ?? "") ||
      left.state.localeCompare(right.state),
  );
}

function recordTranslations(root, sources = []) {
  syncContributing(root);
  const allMappings = sourceTargets(root);
  const unknown = sources.filter((source) => !allMappings.some(([candidate]) => candidate === source));
  if (unknown.length > 0) {
    throw new Error(`Unknown translation sources:\n${unknown.join("\n")}`);
  }
  const mappings = sources.length === 0 ? allMappings : allMappings.filter(([source]) => sources.includes(source));
  const missing = mappings.filter(([, target]) => !existsSync(resolve(root, target)));
  if (missing.length > 0) {
    throw new Error(`Refusing to record missing translations:\n${missing.map(([source]) => source).join("\n")}`);
  }
  const drifted = frontMatterDrift(root, mappings);
  if (drifted.length > 0) {
    throw new Error(
      `Refusing to record translations whose untranslated frontmatter differs from the source:\n${drifted.map(([, target]) => target).join("\n")}`,
    );
  }
  const mismatchedAnchors = sectionAnchorDrift(root, mappings);
  if (mismatchedAnchors.length > 0) {
    throw new Error(
      `Refusing to record translations whose section anchors differ from the source:\n${mismatchedAnchors.map(([, target]) => target).join("\n")}`,
    );
  }
  const currentSources = new Set(allMappings.map(([source]) => source));
  const currentTargets = new Set(allMappings.map(([, target]) => target));
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

  const recorded = Object.fromEntries(
    mappings.map(([source]) => [source, digest(resolve(root, fingerprintSource(source)))]),
  );
  const lock =
    sources.length === 0
      ? recorded
      : Object.fromEntries(
          Object.entries({ ...readLock(root), ...recorded })
            .filter(([source]) => currentSources.has(source))
            .sort(([left], [right]) => left.localeCompare(right)),
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
  const rest = argv.slice(1).filter((_, index) => index + 1 !== rootIndex && index + 1 !== rootIndex + 1);
  return {
    command,
    root: rootIndex === -1 ? resolve(import.meta.dirname, "../..") : resolve(argv[rootIndex + 1]),
    json: argv.includes("--json"),
    sources: rest.filter((argument) => !argument.startsWith("--")),
  };
}

if (import.meta.main) {
  try {
    const { command, root, json, sources } = parseArguments(process.argv.slice(2));
    if (command === "status") {
      const status = translationStatus(root);
      if (status.length > 0) process.exitCode = 1;
      if (json) {
        process.stdout.write(`${JSON.stringify(status, null, 2)}\n`);
      } else if (status.length === 0) {
        console.log("Translations are up to date.");
      } else {
        for (const item of status) console.log(`${item.state}\t${item.source}\t${item.target}`);
      }
    } else if (command === "record") {
      console.log(`Recorded ${recordTranslations(root, sources)} source hashes.`);
    } else {
      console.error(
        "Usage: translation-lock.mjs status [--root PATH] [--json]\n       translation-lock.mjs record [SOURCE...] [--root PATH]",
      );
      process.exitCode = 2;
    }
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
