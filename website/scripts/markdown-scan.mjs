import { existsSync, readdirSync } from "node:fs";
import { relative, resolve, sep } from "node:path";

function toPosix(path) {
  return path.split(sep).join("/");
}

/**
 * 递归列出 `root` 下 `directory` 里的全部 Markdown 文件（.md / .mdx），返回相对 `root` 的
 * POSIX 路径并按字典序排序；目录不存在时返回空数组。
 * 调用方按需要排除生成的 CONTRIBUTING 副本等文件。
 *
 * @param {string} root 绝对路径基准，返回值相对它
 * @param {string} directory 相对 `root` 的目录
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

// CommonMark 允许标题与围栏有 0–3 个前导空格；4 个及以上是缩进代码块，其中的 ``` 不开围栏。
// 开栏捕获完整的重复字符（长度 ≥3），闭栏要求同字符、长度 ≥ 开栏长度、且行内除前导空格与
// 结尾空白外无其他字符——否则四个反引号开出的围栏会被内容里演示用的三个反引号提前闭合。
const FENCE = /^ {0,3}(`{3,}|~{3,})/;
const CLOSING_FENCE = /^ {0,3}(`+|~+)\s*$/;
const HEADING = /^ {0,3}(#{1,6})\s+(.*?)\s*$/;
const JSX_HEADING = /<h[1-6][\s/>]/i;
const ANCHOR_SUFFIX = /\{#([a-z0-9-]+)\}$/;

/**
 * 逐行扫描 Markdown 正文，产出围栏代码块之外每一行的扫描结果。
 *
 * @param {string} content
 * @returns {Generator<{ index: number, line: string, hashes: string | null, text: string | null, anchor: string | null, hasJsxHeading: boolean }>}
 *   `index` 是 0 基行号，`anchor` 为 Markdown 标题末尾的显式锚点。
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
      anchor: heading ? (ANCHOR_SUFFIX.exec(heading[2])?.[1] ?? null) : null,
      hasJsxHeading: JSX_HEADING.test(line),
    };
  }
}
