#!/usr/bin/env node

// Finishes `pnpm write-translations --locale en`: drops the footer copyright snapshot and reports
// every English UI message still carrying Chinese text.

import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { relative, resolve } from "node:path";

const UI_ROOT = "website/i18n/en";
const FOOTER_PATH = `${UI_ROOT}/docusaurus-theme-classic/footer.json`;
const CJK = /\p{Script=Han}/u;

function jsonFiles(directory) {
  if (!existsSync(directory)) return [];
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) return jsonFiles(path);
    return entry.isFile() && entry.name.endsWith(".json") ? [path] : [];
  });
}

// write-translations snapshots themeConfig.footer.copyright, whose year comes from
// `new Date().getFullYear()`, as a static string. Left in place it freezes the English footer's year;
// without it the English locale falls back to the same dynamic config the default locale uses.
function dropFooterCopyright(root) {
  const path = resolve(root, FOOTER_PATH);
  if (!existsSync(path)) return false;
  const messages = JSON.parse(readFileSync(path, "utf8"));
  if (!("copyright" in messages)) return false;
  delete messages.copyright;
  writeFileSync(path, `${JSON.stringify(messages, null, 2)}\n`, "utf8");
  return true;
}

function untranslatedMessages(root) {
  return jsonFiles(resolve(root, UI_ROOT))
    .sort()
    .flatMap((path) =>
      Object.entries(JSON.parse(readFileSync(path, "utf8")))
        .filter(([, entry]) => CJK.test(entry?.message ?? ""))
        .map(([key, entry]) => `${relative(root, path)}\t${key}\t${entry.message}`),
    );
}

const rootIndex = process.argv.indexOf("--root");
const root = rootIndex === -1 ? process.cwd() : resolve(process.argv[rootIndex + 1]);

if (dropFooterCopyright(root)) console.log(`Removed the generated copyright key from ${FOOTER_PATH}.`);
const untranslated = untranslatedMessages(root);
if (untranslated.length === 0) {
  console.log("UI messages are translated.");
} else {
  for (const line of untranslated) console.log(line);
  process.exitCode = 1;
}
