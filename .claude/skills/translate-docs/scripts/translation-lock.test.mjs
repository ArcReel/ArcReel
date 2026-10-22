import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const scriptPath = join(dirname(fileURLToPath(import.meta.url)), "translation-lock.mjs");

function write(root, relativePath, contents) {
  const path = join(root, relativePath);
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, contents, "utf8");
}

function status(root) {
  const output = execFileSync(process.execPath, [scriptPath, "status", "--root", root, "--json"], {
    encoding: "utf8",
  });
  return JSON.parse(output);
}

function record(root) {
  execFileSync(process.execPath, [scriptPath, "record", "--root", root], { encoding: "utf8" });
}

test("status reports every untranslated documentation source", () => {
  const root = mkdtempSync(join(tmpdir(), "arcreel-translation-lock-"));
  write(root, "website/docs/index.mdx", "# 首页 {#home}\n");
  write(root, "website/docs/guide/start.md", "# 入门 {#start}\n");
  write(root, "CONTRIBUTING.md", "# 贡献\n");
  write(root, "README.md", "# ArcReel\n");

  assert.deepEqual(status(root), [
    {
      source: "CONTRIBUTING.md",
      target: "website/i18n/en/docusaurus-plugin-content-docs/current/dev/contributing.md",
      state: "missing",
    },
    { source: "README.md", target: "README.en.md", state: "missing" },
    {
      source: "website/docs/guide/start.md",
      target: "website/i18n/en/docusaurus-plugin-content-docs/current/guide/start.md",
      state: "missing",
    },
    {
      source: "website/docs/index.mdx",
      target: "website/i18n/en/docusaurus-plugin-content-docs/current/index.mdx",
      state: "missing",
    },
  ]);
});

test("record writes LF-normalized source hashes to the flat lockfile", () => {
  const root = mkdtempSync(join(tmpdir(), "arcreel-translation-lock-"));
  write(root, "README.md", "hello\r\n");
  write(root, "README.en.md", "hello\n");

  record(root);

  assert.equal(
    readFileSync(join(root, "website/i18n/translation.lock.json"), "utf8"),
    '{\n  "README.md": "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"\n}\n',
  );
  assert.deepEqual(status(root), []);
});

test("status reports translations whose sources were deleted", () => {
  const root = mkdtempSync(join(tmpdir(), "arcreel-translation-lock-"));
  write(root, "README.md", "# ArcReel\n");
  write(root, "README.en.md", "# ArcReel\n");
  write(root, "website/docs/guide/start.md", "# 入门\n");
  write(root, "website/i18n/en/docusaurus-plugin-content-docs/current/guide/start.md", "# Getting started\n");
  record(root);
  renameSync(join(root, "README.md"), join(root, "README.deleted"));
  renameSync(join(root, "website/docs/guide/start.md"), join(root, "website/docs/guide/start.deleted"));

  assert.deepEqual(status(root), [
    { source: "README.md", target: "README.en.md", state: "orphan" },
    {
      source: "website/docs/guide/start.md",
      target: "website/i18n/en/docusaurus-plugin-content-docs/current/guide/start.md",
      state: "orphan",
    },
  ]);
});

test("status reports unregistered document translations but ignores locale assets", () => {
  const root = mkdtempSync(join(tmpdir(), "arcreel-translation-lock-"));
  write(
    root,
    "website/i18n/vi/docusaurus-plugin-content-docs/current/guide/unregistered.md",
    "# Unregistered\n",
  );
  write(root, "website/i18n/en/docusaurus-plugin-content-docs/current.json", '{"title":"Docs"}\n');
  write(root, "website/i18n/en/docusaurus-theme-classic/navbar.json", '{"title":"Docs"}\n');
  write(root, "website/i18n/en/code.json", '{"theme.ErrorPageContent.title":"Error"}\n');

  assert.deepEqual(status(root), [
    {
      source: "website/docs/guide/unregistered.md",
      target: "website/i18n/vi/docusaurus-plugin-content-docs/current/guide/unregistered.md",
      state: "orphan",
    },
  ]);
});

test("status reports a translation as stale when its lock entry was removed", () => {
  const root = mkdtempSync(join(tmpdir(), "arcreel-translation-lock-"));
  write(root, "website/docs/guide/start.md", "# 入门\n");
  write(root, "website/i18n/en/docusaurus-plugin-content-docs/current/guide/start.md", "# Getting started\n");
  record(root);
  write(root, "website/i18n/translation.lock.json", "{}\n");

  assert.deepEqual(status(root), [
    {
      source: "website/docs/guide/start.md",
      target: "website/i18n/en/docusaurus-plugin-content-docs/current/guide/start.md",
      state: "stale",
    },
  ]);
});

test("status reports a translated source whose recorded content changed", () => {
  const root = mkdtempSync(join(tmpdir(), "arcreel-translation-lock-"));
  write(root, "README.md", "# ArcReel\n");
  write(root, "README.en.md", "# ArcReel\n");
  record(root);
  write(root, "README.md", "# ArcReel video creation\n");

  assert.deepEqual(status(root), [
    { source: "README.md", target: "README.en.md", state: "stale" },
  ]);
});

test("status reports CONTRIBUTING.md stale when only its synced copy changed", () => {
  const root = mkdtempSync(join(tmpdir(), "arcreel-translation-lock-"));
  write(root, "CONTRIBUTING.md", "# 贡献\n");
  write(root, "website/docs/dev/contributing.md", "---\nid: contributing\n---\n\n# 贡献 {#contributing}\n");
  write(root, "website/i18n/en/docusaurus-plugin-content-docs/current/dev/contributing.md", "---\nid: contributing\n---\n\n# Contributing {#contributing}\n");
  record(root);
  write(root, "website/docs/dev/contributing.md", "---\nid: contributing\n---\n\n# 贡献 {#contribute}\n");

  assert.deepEqual(status(root), [
    {
      source: "CONTRIBUTING.md",
      target: "website/i18n/en/docusaurus-plugin-content-docs/current/dev/contributing.md",
      state: "stale",
    },
  ]);
});

test("record refuses to hide a missing translation", () => {
  const root = mkdtempSync(join(tmpdir(), "arcreel-translation-lock-"));
  write(root, "README.md", "# ArcReel\n");

  const result = spawnSync(process.execPath, [scriptPath, "record", "--root", root], {
    encoding: "utf8",
  });

  assert.equal(result.status, 1);
  assert.match(result.stderr, /Refusing to record missing translations:\nREADME\.md/);
  assert.equal(existsSync(join(root, "website/i18n/translation.lock.json")), false);
});

test("record refuses to hide an orphan translation that still exists", () => {
  const root = mkdtempSync(join(tmpdir(), "arcreel-translation-lock-"));
  write(root, "README.md", "# ArcReel\n");
  write(root, "README.en.md", "# ArcReel\n");
  record(root);
  renameSync(join(root, "README.md"), join(root, "README.deleted"));

  const result = spawnSync(process.execPath, [scriptPath, "record", "--root", root], {
    encoding: "utf8",
  });

  assert.equal(result.status, 1);
  assert.match(result.stderr, /Refusing to record orphan translations:\nREADME\.en\.md/);
});

test("record refuses to hide an unregistered document translation", () => {
  const root = mkdtempSync(join(tmpdir(), "arcreel-translation-lock-"));
  write(root, "website/i18n/en/docusaurus-plugin-content-docs/current/guide/unregistered.md", "# Unregistered\n");

  const result = spawnSync(process.execPath, [scriptPath, "record", "--root", root], {
    encoding: "utf8",
  });

  assert.equal(result.status, 1);
  assert.match(
    result.stderr,
    /Refusing to record orphan translations:\nwebsite\/i18n\/en\/docusaurus-plugin-content-docs\/current\/guide\/unregistered\.md/,
  );
});

test("status reports a translation whose untranslated frontmatter drifted from the source", () => {
  const root = mkdtempSync(join(tmpdir(), "arcreel-translation-lock-"));
  write(root, "website/docs/guide/start.md", "---\nid: start\ntitle: 入门\nupdate_docs: full\n---\n\n# 入门 {#start}\n");
  write(
    root,
    "website/i18n/en/docusaurus-plugin-content-docs/current/guide/start.md",
    "---\nid: start\ntitle: Getting Started\n---\n\n# Getting Started {#start}\n",
  );

  assert.deepEqual(status(root), [
    {
      source: "website/docs/guide/start.md",
      target: "website/i18n/en/docusaurus-plugin-content-docs/current/guide/start.md",
      state: "frontmatter",
    },
    {
      source: "website/docs/guide/start.md",
      target: "website/i18n/en/docusaurus-plugin-content-docs/current/guide/start.md",
      state: "stale",
    },
  ]);
});

test("status accepts a translation that differs only in translated frontmatter values", () => {
  const root = mkdtempSync(join(tmpdir(), "arcreel-translation-lock-"));
  write(
    root,
    "website/docs/guide/start.md",
    "---\nid: start\ntitle: 入门\ndescription: >-\n  中文描述\nupdate_docs: full\n---\n\n# 入门 {#start}\n",
  );
  write(
    root,
    "website/i18n/en/docusaurus-plugin-content-docs/current/guide/start.md",
    "---\nid: start\ntitle: Getting Started\ndescription: >-\n  English description\nupdate_docs: full\n---\n\n# Getting Started {#start}\n",
  );
  record(root);

  assert.deepEqual(status(root), []);
});

test("record refuses to hide frontmatter drift", () => {
  const root = mkdtempSync(join(tmpdir(), "arcreel-translation-lock-"));
  write(root, "website/docs/guide/start.md", "---\nid: start\nsidebar_position: 1\n---\n\n# 入门\n");
  write(
    root,
    "website/i18n/en/docusaurus-plugin-content-docs/current/guide/start.md",
    "---\nid: start\nsidebar_position: 2\n---\n\n# Getting Started\n",
  );

  const result = spawnSync(process.execPath, [scriptPath, "record", "--root", root], {
    encoding: "utf8",
  });

  assert.equal(result.status, 1);
  assert.match(result.stderr, /untranslated frontmatter differs from the source:\nwebsite\/i18n\/en\//);
  assert.equal(existsSync(join(root, "website/i18n/translation.lock.json")), false);
});

test("record refuses to hide drift in a quoted frontmatter key", () => {
  const root = mkdtempSync(join(tmpdir(), "arcreel-translation-lock-"));
  write(root, "website/docs/guide/start.md", '---\ntitle: 入门\n"update_docs": full\n---\n\n# 入门\n');
  write(
    root,
    "website/i18n/en/docusaurus-plugin-content-docs/current/guide/start.md",
    "---\ntitle: Getting Started\n---\n\n# Getting Started\n",
  );

  const result = spawnSync(process.execPath, [scriptPath, "record", "--root", root], {
    encoding: "utf8",
  });

  assert.equal(result.status, 1);
  assert.match(result.stderr, /untranslated frontmatter differs from the source/);
});

test("record with a source refreshes only that entry and keeps the others as recorded", () => {
  const root = mkdtempSync(join(tmpdir(), "arcreel-translation-lock-"));
  const target = (page) => `website/i18n/en/docusaurus-plugin-content-docs/current/guide/${page}.md`;
  write(root, "website/docs/guide/a.md", "# 甲\n");
  write(root, "website/docs/guide/b.md", "# 乙\n");
  write(root, target("a"), "# A\n");
  write(root, target("b"), "# B\n");
  record(root);
  const lockPath = join(root, "website/i18n/translation.lock.json");
  const before = JSON.parse(readFileSync(lockPath, "utf8"));
  write(root, "website/docs/guide/a.md", "# 甲 changed\n");
  write(root, "website/docs/guide/b.md", "# 乙 changed\n");

  execFileSync(process.execPath, [scriptPath, "record", "website/docs/guide/a.md", "--root", root], { encoding: "utf8" });

  const after = JSON.parse(readFileSync(lockPath, "utf8"));
  assert.notEqual(after["website/docs/guide/a.md"], before["website/docs/guide/a.md"]);
  assert.equal(after["website/docs/guide/b.md"], before["website/docs/guide/b.md"]);
  assert.deepEqual(status(root), [{ source: "website/docs/guide/b.md", target: target("b"), state: "stale" }]);
});

test("record with a source drops entries whose source and target were both deleted", () => {
  const root = mkdtempSync(join(tmpdir(), "arcreel-translation-lock-"));
  const target = (page) => `website/i18n/en/docusaurus-plugin-content-docs/current/guide/${page}.md`;
  write(root, "website/docs/guide/a.md", "# 甲\n");
  write(root, "website/docs/guide/c.md", "# 丙\n");
  write(root, target("a"), "# A\n");
  write(root, target("c"), "# C\n");
  record(root);
  rmSync(join(root, "website/docs/guide/c.md"));
  rmSync(join(root, target("c")));
  write(root, "website/docs/guide/a.md", "# 甲 changed\n");

  execFileSync(process.execPath, [scriptPath, "record", "website/docs/guide/a.md", "--root", root], { encoding: "utf8" });

  const after = JSON.parse(readFileSync(join(root, "website/i18n/translation.lock.json"), "utf8"));
  assert.deepEqual(Object.keys(after), ["website/docs/guide/a.md"]);
  assert.deepEqual(status(root), []);
});

test("record with a source refuses a source that is not a documentation source", () => {
  const root = mkdtempSync(join(tmpdir(), "arcreel-translation-lock-"));
  write(root, "README.md", "# ArcReel\n");
  write(root, "README.en.md", "# ArcReel\n");

  const result = spawnSync(process.execPath, [scriptPath, "record", "website/docs/nope.md", "--root", root], {
    encoding: "utf8",
  });

  assert.equal(result.status, 1);
  assert.match(result.stderr, /website\/docs\/nope\.md/);
  assert.equal(existsSync(join(root, "website/i18n/translation.lock.json")), false);
});

test("status generates the missing synced CONTRIBUTING.md copy with the repository sync script", () => {
  const root = mkdtempSync(join(tmpdir(), "arcreel-translation-lock-"));
  write(root, "CONTRIBUTING.md", "# 贡献\n");
  write(
    root,
    "website/scripts/sync-contributing.mjs",
    'import { writeFileSync, mkdirSync } from "node:fs";\nmkdirSync("website/docs/dev", { recursive: true });\nwriteFileSync("website/docs/dev/contributing.md", "# 贡献\\n");\n',
  );
  write(root, "website/i18n/en/docusaurus-plugin-content-docs/current/dev/contributing.md", "# Contributing\n");

  assert.deepEqual(status(root), [
    {
      source: "CONTRIBUTING.md",
      target: "website/i18n/en/docusaurus-plugin-content-docs/current/dev/contributing.md",
      state: "stale",
    },
  ]);
  assert.equal(existsSync(join(root, "website/docs/dev/contributing.md")), true);
});

test("status explains how to generate the synced copy when no sync script exists", () => {
  const root = mkdtempSync(join(tmpdir(), "arcreel-translation-lock-"));
  write(root, "CONTRIBUTING.md", "# 贡献\n");
  write(root, "website/i18n/en/docusaurus-plugin-content-docs/current/dev/contributing.md", "# Contributing\n");

  const result = spawnSync(process.execPath, [scriptPath, "status", "--root", root], { encoding: "utf8" });

  assert.equal(result.status, 1);
  assert.match(result.stderr, /pnpm sync-contributing/);
});
