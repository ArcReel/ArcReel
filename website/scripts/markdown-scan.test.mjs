import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";

import { walkMarkdownFiles } from "./markdown-scan.mjs";

function withTree(files, run) {
  const root = mkdtempSync(join(tmpdir(), "arcreel-markdown-scan-"));
  try {
    for (const path of files) {
      const absolutePath = join(root, path);
      mkdirSync(resolve(absolutePath, ".."), { recursive: true });
      writeFileSync(absolutePath, "content\n", "utf8");
    }
    run(root);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

test("walkMarkdownFiles recurses, keeps only .md/.mdx, and sorts POSIX-relative paths", () => {
  withTree(
    [
      "docs/zebra.md",
      "docs/alpha.mdx",
      "docs/nested/deep/page.md",
      "docs/nested/_category_.json",
      "docs/readme.txt",
      "outside.md",
    ],
    (root) => {
      assert.deepEqual(walkMarkdownFiles(root, "docs"), [
        "docs/alpha.mdx",
        "docs/nested/deep/page.md",
        "docs/zebra.md",
      ]);
    },
  );
});

test("walkMarkdownFiles returns an empty list for a missing directory", () => {
  withTree([], (root) => {
    assert.deepEqual(walkMarkdownFiles(root, "no-such-dir"), []);
  });
});
