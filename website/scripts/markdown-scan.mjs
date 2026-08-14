// 文档脚本共用的 Markdown 逐行扫描：识别围栏代码块并跳过其内容，对正文行给出标题解析结果。
// 围栏与标题的识别规则只在这里定义一次——check-consistency 与 sync-contributing 必须对
// 「哪一行算标题」给出同一答案，否则一处认得的标题在另一处漏检。

// CommonMark 允许标题与围栏有 0–3 个前导空格；4 个及以上是缩进代码块，其中的 ``` 不开围栏。
const FENCE = /^ {0,3}(```|~~~)/;
const HEADING = /^ {0,3}(#{1,6})\s+(.*?)\s*$/;
const JSX_HEADING = /<h[1-6][\s/>]/i;

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
    const fenceMatch = FENCE.exec(line);
    if (fenceMatch) {
      if (!fence) fence = fenceMatch[1];
      else if (line.trim().startsWith(fence)) fence = "";
      continue;
    }
    if (fence) continue;

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
