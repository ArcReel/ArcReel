// 文档脚本共用的 Markdown 基础解析：读取简单 frontmatter 标量，并在逐行扫描时识别围栏代码块、
// 跳过其内容，对正文行给出标题解析结果。
// 围栏与标题的识别规则只在这里定义一次——check-consistency 与 sync-contributing 必须对
// 「哪一行算标题」给出同一答案，否则一处认得的标题在另一处漏检。

// CommonMark 允许标题与围栏有 0–3 个前导空格；4 个及以上是缩进代码块，其中的 ``` 不开围栏。
// 开栏捕获完整的重复字符（长度 ≥3），闭栏要求同字符、长度 ≥ 开栏长度、且行内除前导空格与
// 结尾空白外无其他字符——否则四个反引号开出的围栏会被内容里演示用的三个反引号提前闭合。
const FENCE = /^ {0,3}(`{3,}|~{3,})/;
const CLOSING_FENCE = /^ {0,3}(`+|~+)\s*$/;
const HEADING = /^ {0,3}(#{1,6})\s+(.*?)\s*$/;
const JSX_HEADING = /<h[1-6][\s/>]/i;

/**
 * 读取文档开头 YAML frontmatter 里的顶层标量。只负责供仓库脚本读取自有的简单 key；复杂 YAML
 * 仍交给 Docusaurus。缺少 frontmatter / key 时返回 null，声明成对象或空值时返回空字符串，供调用方
 * 给出针对具体 key 的 fail-loud 信息。
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
 * 逐行扫描 Markdown 正文，产出围栏代码块之外每一行的扫描结果。
 *
 * @param {string} content
 * @returns {Generator<{ index: number, line: string, hashes: string | null, text: string | null, hasJsxHeading: boolean }>}
 *   `index` 是 0 基行号；非标题行的 `hashes` / `text` 为 null。
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
