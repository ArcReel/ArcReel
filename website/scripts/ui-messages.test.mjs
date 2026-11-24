import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const scriptPath = join(dirname(fileURLToPath(import.meta.url)), "ui-messages.mjs");

function write(root, relativePath, messages) {
  const path = join(root, relativePath);
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${JSON.stringify(messages, null, 2)}\n`, "utf8");
}

function run(root) {
  return spawnSync(process.execPath, [scriptPath, "--root", root], { encoding: "utf8" });
}

test("removes the generated footer copyright and keeps the other footer messages", () => {
  const root = mkdtempSync(join(tmpdir(), "arcreel-ui-messages-"));
  write(root, "website/i18n/en/docusaurus-theme-classic/footer.json", {
    "link.item.label.GitHub 仓库": { message: "GitHub Repository", description: "GitHub 仓库" },
    copyright: { message: "Copyright © 2026 ArcReel.", description: "The footer copyright" },
  });

  const result = run(root);

  assert.equal(result.status, 0);
  assert.deepEqual(
    JSON.parse(readFileSync(join(root, "website/i18n/en/docusaurus-theme-classic/footer.json"), "utf8")),
    { "link.item.label.GitHub 仓库": { message: "GitHub Repository", description: "GitHub 仓库" } },
  );
});

test("reports every message that still carries Chinese text, ignoring keys and descriptions", () => {
  const root = mkdtempSync(join(tmpdir(), "arcreel-ui-messages-"));
  write(root, "website/i18n/en/code.json", {
    "theme.done": { message: "Done", description: "完成" },
    "theme.search": { message: "搜索", description: "Search" },
    "theme.rare": { message: "\u{20000}", description: "Supplementary-plane Han" },
  });
  write(root, "website/i18n/en/docusaurus-theme-classic/navbar.json", {
    "item.label.指南": { message: "指南" },
  });

  const result = run(root);

  assert.equal(result.status, 1);
  assert.equal(
    result.stdout,
    "website/i18n/en/code.json\ttheme.search\t搜索\n" +
      "website/i18n/en/code.json\ttheme.rare\t\u{20000}\n" +
      "website/i18n/en/docusaurus-theme-classic/navbar.json\titem.label.指南\t指南\n",
  );
});
