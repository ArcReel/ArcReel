import { render, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StreamMarkdown } from "./StreamMarkdown";

const URL_ATTRS = ["href", "src", "xlink:href", "action", "formaction", "srcdoc", "data"];
const ACTIVE_TAGS = ["script", "iframe", "object", "embed", "form", "svg", "math", "style", "base", "meta", "link"];

// 按浏览器 URL 解析规则归一：去掉首尾 C0 控制符与空白、剔除中间的制表与换行后取协议。
function urlScheme(value: string): string {
  let start = 0;
  let end = value.length;
  while (start < end && value.charCodeAt(start) <= 0x20) start += 1;
  while (end > start && value.charCodeAt(end - 1) <= 0x20) end -= 1;
  const normalized = value.slice(start, end).replace(/[\t\n\r]/g, "");
  const match = /^([a-z][a-z0-9+.-]*):/i.exec(normalized);
  return match ? match[1].toLowerCase() : "";
}

function collectActiveContent(root: HTMLElement): string[] {
  const findings: string[] = [];
  for (const el of root.querySelectorAll("*")) {
    const tag = el.tagName.toLowerCase();
    if (ACTIVE_TAGS.includes(tag)) findings.push(`<${tag}>`);
    for (const attr of el.attributes) {
      const name = attr.name.toLowerCase();
      if (name.startsWith("on")) findings.push(`${tag}[${name}]`);
      if (name === "style" && /expression\(|url\(/i.test(attr.value)) {
        findings.push(`${tag}[style=${attr.value}]`);
      }
      if (URL_ATTRS.includes(name)) {
        const scheme = urlScheme(attr.value);
        if (scheme === "javascript" || scheme === "vbscript") {
          findings.push(`${tag}[${name}=${attr.value}]`);
        }
        if (scheme === "data" && !/^\s*data:image\/(png|jpe?g|gif|webp);/i.test(attr.value)) {
          findings.push(`${tag}[${name}=${attr.value}]`);
        }
      }
    }
  }
  return findings;
}

async function renderLoaded(content: string) {
  const view = render(<StreamMarkdown content={content} />);
  await waitFor(() => {
    expect(view.container.querySelector(".markdown-body")).not.toBeNull();
  });
  return view;
}

const PAYLOADS: Record<string, string> = {
  "script 块": "<script>window.__marker = 1</script>",
  "img 事件属性": '<img src=x onerror="window.__marker = 1">',
  "svg 事件属性": '<svg onload="window.__marker = 1"><circle r="1"/></svg>',
  "math 内嵌 img": '<math><mtext><img src=x onerror="window.__marker = 1"></mtext></math>',
  "iframe srcdoc": '<iframe srcdoc="<script>window.__marker = 1</script>"></iframe>',
  "链接 javascript 协议": "[x](javascript:window.__marker=1)",
  "链接 data html": "[x](data:text/html,<script>window.__marker=1</script>)",
  "图片 javascript 协议": "![x](javascript:window.__marker=1)",
  "链接协议大小写混合": "[x](JaVaScRiPt:window.__marker=1)",
  "链接协议含制表符": "[x](java\tscript:window.__marker=1)",
  "链接协议前导控制字符": "[x](\u0001javascript:window.__marker=1)",
  "链接协议实体编码": "[x](&#106;avascript:window.__marker=1)",
  "链接协议冒号实体编码": "[x](javascript&colon;window.__marker=1)",
  "链接协议十六进制实体": "[x](&#x6A;&#x61;vascript:window.__marker=1)",
  "引用式链接": "[x][r]\n\n[r]: javascript:window.__marker=1",
  "raw HTML 块链接": '<div>\n<a href="javascript:window.__marker=1">x</a>\n</div>',
  "行内 HTML 链接": 'text <a href="javascript:window.__marker=1">x</a> text',
  "行内 HTML 事件属性": 'text <b onclick="window.__marker=1">x</b> text',
  "行内 HTML 实体编码协议": 'text <a href="&#106;avascript:window.__marker=1">x</a>',
  "autolink javascript": "<javascript:window.__marker=1>",
  "autolink data": "<data:text/html,window.__marker=1>",
  "form formaction": '<form><button formaction="javascript:window.__marker=1">x</button></form>',
  "style 标签": "<style>body{background:url(javascript:window.__marker=1)}</style>",
};

describe("StreamMarkdown 渲染惰性", () => {
  it.each(Object.entries(PAYLOADS))("%s 渲染为惰性内容", async (_name, payload) => {
    const { container } = await renderLoaded(`前文\n\n${payload}\n\n后文`);
    expect(collectActiveContent(container)).toEqual([]);
  });

  it.each(Object.entries(PAYLOADS))("%s 在任意位置分两段增量渲染时每一步都是惰性内容", async (_name, payload) => {
    const { container, rerender } = await renderLoaded("");
    for (let cut = 1; cut < payload.length; cut += 1) {
      rerender(<StreamMarkdown content={payload.slice(0, cut)} />);
      expect(collectActiveContent(container), `cut=${cut} 前段`).toEqual([]);
      rerender(<StreamMarkdown content={payload} />);
      expect(collectActiveContent(container), `cut=${cut} 全文`).toEqual([]);
    }
  });

  it("外链渲染为不带 href 的按钮，https 图片保留地址", async () => {
    const { container } = await renderLoaded("[站点](https://example.com/a)\n\n![图](https://example.com/b.png)");
    const link = container.querySelector('[data-streamdown="link"]');
    expect(link?.tagName).toBe("BUTTON");
    expect(container.querySelector("a[href]")).toBeNull();
    expect(container.querySelector("img")?.getAttribute("src")).toBe("https://example.com/b.png");
    expect(collectActiveContent(container)).toEqual([]);
  });
});
