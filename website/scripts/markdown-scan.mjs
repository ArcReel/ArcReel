// Shared Markdown primitives for documentation scripts: traverse directories, read simple frontmatter scalars, and recognize
// fenced code blocks during line scans, skipping their contents and parsing headings on body lines.
// Define fence/heading recognition once here: check-consistency and sync-contributing must agree
// on which lines count as headings, or one would miss headings recognized by the other.
// Likewise, define which files count as documents only here: separate implementations for page inventory, anchor scanning, and translation registration
// would let an exclusion added to one make gates contradict each other (one passes, another fails).

import { existsSync, readdirSync } from "node:fs";
import { relative, resolve, sep } from "node:path";

function toPosix(path) {
  return path.split(sep).join("/");
}

/**
 * Recursively list all Markdown files (.md / .mdx) in `directory` under `root`, returning lexicographically sorted
 * POSIX paths relative to `root`; return an empty array if the directory does not exist.
 * This function decides only which files count as Markdown documents. Callers narrow the result for gate-specific exclusions
 * (e.g. generated CONTRIBUTING copies are excluded from page inventory and translation registration but included in anchor scans).
 *
 * @param {string} root Absolute path base; results are relative to it
 * @param {string} directory Directory relative to `root`
 * @returns {string[]}
 */
export function walkMarkdownFiles(root, directory) {
  const absoluteDirectory = resolve(root, directory);
  if (!existsSync(absoluteDirectory)) return [];
  return readdirSync(absoluteDirectory, { withFileTypes: true })
    .flatMap((entry) => {
      const path = resolve(absoluteDirectory, entry.name);
      if (entry.isDirectory()) return walkMarkdownFiles(root, toPosix(relative(root, path)));
      if (!entry.isFile() || !/\.mdx?$/.test(entry.name)) return [];
      return [toPosix(relative(root, path))];
    })
    .sort();
}

// CommonMark allows 0–3 leading spaces for headings/fences; 4+ spaces form indented code, where ``` does not open a fence.
// Opening fences capture the full repeated character sequence (length ≥3); closing fences require the same character, length ≥ opening length,
// and nothing else except leading spaces/trailing whitespace, preventing three demonstration backticks from prematurely closing a four-backtick fence.
const FENCE = /^ {0,3}(`{3,}|~{3,})/;
const CLOSING_FENCE = /^ {0,3}(`+|~+)\s*$/;
const HEADING = /^ {0,3}(#{1,6})\s+(.*?)\s*$/;
const JSX_HEADING = /<h[1-6][\s/>]/i;

/**
 * Read top-level scalars from opening YAML frontmatter. This handles only simple repository-owned keys read by scripts; leave complex YAML
 * to Docusaurus. Return null for missing frontmatter/keys; return an empty string for object or null declarations, allowing callers
 * to fail loudly with key-specific diagnostics.
 *
 * @param {string} content
 * @param {string} key
 * @returns {string | null}
 */
export function readFrontMatterScalar(content, key) {
  const lines = content.replace(/^\uFEFF/, "").split("\n");
  if (lines[0]?.trim() !== "---") return null;

  for (let index = 1; index < lines.length; index += 1) {
    const line = lines[index].replace(/\r$/, "");
    if (line.trim() === "---") return null;

    const match = /^([A-Za-z][A-Za-z0-9_-]*):(?:\s*(.*?))?\s*$/.exec(line);
    if (!match || match[1] !== key) continue;
    const value = match[2] ?? "";
    const quote = value[0];
    if ((quote === '"' || quote === "'") && value.at(-1) === quote) return value.slice(1, -1);
    return value;
  }
  return null;
}

/**
 * Scan Markdown body lines, yielding results for every line outside fenced code blocks.
 *
 * @param {string} content
 * @returns {Generator<{ index: number, line: string, hashes: string | null, text: string | null, hasJsxHeading: boolean }>}
 *   `index` is a zero-based line number; `hashes` / `text` are null on non-heading lines.
 */
export function* scanMarkdownLines(content) {
  let fence = "";

  for (const [index, line] of content.split("\n").entries()) {
    if (fence) {
      const closingMatch = CLOSING_FENCE.exec(line);
      if (closingMatch && closingMatch[1][0] === fence[0] && closingMatch[1].length >= fence.length) fence = "";
      continue;
    }
    const fenceMatch = FENCE.exec(line);
    if (fenceMatch) {
      fence = fenceMatch[1];
      continue;
    }

    const heading = HEADING.exec(line);
    yield {
      index,
      line,
      hashes: heading ? heading[1] : null,
      text: heading ? heading[2] : null,
      hasJsxHeading: JSX_HEADING.test(line),
    };
  }
}
