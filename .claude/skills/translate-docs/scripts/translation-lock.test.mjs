import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
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
  record(root);
  renameSync(join(root, "README.md"), join(root, "README.deleted"));

  assert.deepEqual(status(root), [
    { source: "README.md", target: "README.en.md", state: "orphan" },
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

test("status reports a translation as orphan when its lock entry was removed", () => {
  const root = mkdtempSync(join(tmpdir(), "arcreel-translation-lock-"));
  write(root, "website/docs/guide/start.md", "# 入门\n");
  write(root, "website/i18n/en/docusaurus-plugin-content-docs/current/guide/start.md", "# Getting started\n");
  record(root);
  write(root, "website/i18n/translation.lock.json", "{}\n");

  assert.deepEqual(status(root), [
    {
      source: "website/docs/guide/start.md",
      target: "website/i18n/en/docusaurus-plugin-content-docs/current/guide/start.md",
      state: "orphan",
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
  write(root, "website/i18n/en/docusaurus-plugin-content-docs/current/dev/contributing.md", "# Contributing\n");
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
