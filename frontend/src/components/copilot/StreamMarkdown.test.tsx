import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
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
        const activeData = scheme === "data" && !/^\s*data:image\/(png|jpe?g|gif|webp);/i.test(attr.value);
        if (scheme === "javascript" || scheme === "vbscript" || activeData) {
          findings.push(`${tag}[${name}=${attr.value}]`);
        }
      }
    }
  }
  return findings;
}

// 链接按钮不带 href，地址只保存在点击回调里，DOM 属性检查看不到；PAYLOADS 不含允许的链接，完整渲染后不应出现可点击元素。
function clickableElements(root: HTMLElement): string[] {
  return Array.from(
    root.querySelectorAll('a, button, [data-streamdown="link"]'),
    (el) => `<${el.tagName.toLowerCase()}>${el.textContent ?? ""}`,
  );
}

async function renderLoaded(content: string) {
  const view = render(<StreamMarkdown content={content} />);
  // 等实际按需模块加载完成，再断言 Markdown 结构，不把加载耗时计入查询超时。
  await act(async () => { await import("streamdown"); });
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
  "图片 data html": "![x](data:text/html,<script>window.__marker=1</script>)",
  "图片 data svg": "![x](data:image/svg+xml,%3Csvg%20onload%3D%22window.__marker%3D1%22%2F%3E)",
  "图片协议大小写混合": "![x](JaVaScRiPt:window.__marker=1)",
  "图片协议含制表符": "![x](java\tscript:window.__marker=1)",
  "图片协议实体编码": "![x](&#106;avascript:window.__marker=1)",
  "图片协议实体编码制表符": "![x](java&#9;script:window.__marker=1)",
  "图片 data 大小写混合": "![x](DaTa:text/html,window.__marker=1)",
  "图片 data 实体编码": "![x](&#100;ata:text/html,window.__marker=1)",
  "图片 data 十六进制实体": "![x](&#x64;ata:text/html,window.__marker=1)",
  "图片 data 实体编码换行": "![x](da&#10;ta:text/html,window.__marker=1)",
  "链接协议大小写混合": "[x](JaVaScRiPt:window.__marker=1)",
  "链接协议含制表符": "[x](java\tscript:window.__marker=1)",
  "链接协议含换行": "[x](<java\nscript:window.__marker=1>)",
  "链接协议含回车": "[x](<java\rscript:window.__marker=1>)",
  "链接协议前导空格": "[x]( javascript:window.__marker=1)",
  "链接协议前导控制字符": "[x](\u0001javascript:window.__marker=1)",
  "链接协议实体编码": "[x](&#106;avascript:window.__marker=1)",
  "链接协议冒号实体编码": "[x](javascript&colon;window.__marker=1)",
  "链接协议十六进制实体": "[x](&#x6A;&#x61;vascript:window.__marker=1)",
  "链接协议实体编码制表符": "[x](java&#9;script:window.__marker=1)",
  "链接协议实体编码换行": "[x](java&#10;script:window.__marker=1)",
  "链接协议实体编码前导控制字符": "[x](&#1;javascript:window.__marker=1)",
  "链接 data 大小写混合": "[x](DaTa:text/html,window.__marker=1)",
  "链接 data 实体编码": "[x](&#100;ata:text/html,window.__marker=1)",
  "链接 data 十六进制实体": "[x](&#x64;ata:text/html,window.__marker=1)",
  "链接 data 实体编码换行": "[x](da&#10;ta:text/html,window.__marker=1)",
  "引用式链接": "[x][r]\n\n[r]: javascript:window.__marker=1",
  "raw HTML 块链接": '<div>\n<a href="javascript:window.__marker=1">x</a>\n</div>',
  "行内 HTML 链接": 'text <a href="javascript:window.__marker=1">x</a> text',
  "行内 HTML 事件属性": 'text <b onclick="window.__marker=1">x</b> text',
  "行内 HTML 实体编码协议": 'text <a href="&#106;avascript:window.__marker=1">x</a>',
  "应用链接 javascript 协议": '<app-link href="javascript:window.__marker=1" onclick="window.__marker=1">x</app-link>',
  "应用链接实体编码协议": '<app-link href="&#106;avascript:window.__marker=1">x</app-link>',
  "autolink javascript": "<javascript:window.__marker=1>",
  "裸 URL autolink": "见 javascript:window.__marker=1 与后文",
  "autolink data": "<data:text/html,window.__marker=1>",
  "form formaction": '<form><button formaction="javascript:window.__marker=1">x</button></form>',
  "style 标签": "<style>body{background:url(javascript:window.__marker=1)}</style>",
};

describe("StreamMarkdown 横向滚动区", () => {
  it("代码块与表格可用键盘聚焦，并带有区域名称", async () => {
    await renderLoaded("```ts\nconst answer = 42;\n```\n\n| 列 A | 列 B |\n| --- | --- |\n| 1 | 2 |\n");

    const code = await screen.findByRole("region", { name: "代码块" });
    expect(code).toHaveAttribute("tabindex", "0");
    expect(code.querySelector("pre")).not.toBeNull();
    expect(screen.getByRole("table", { name: "表格" })).toHaveAttribute("tabindex", "0");
  });

  it("切换界面语言后，已渲染的代码块与表格换成新语言的名称", async () => {
    await renderLoaded("```ts\nconst answer = 42;\n```\n\n| 列 A | 列 B |\n| --- | --- |\n| 1 | 2 |\n");
    expect(await screen.findByRole("region", { name: "代码块" })).toBeInTheDocument();

    try {
      await act(async () => {
        await i18n.changeLanguage("en");
      });
      expect(await screen.findByRole("region", { name: "Code block" })).toHaveTextContent("const answer = 42;");
      expect(screen.getByRole("table", { name: "Table" })).toBeInTheDocument();
    } finally {
      await act(async () => {
        await i18n.changeLanguage("zh");
      });
    }
  });
});

describe("StreamMarkdown 工具条", () => {
  it("代码块与表格工具条按钮的名称跟随界面语言", async () => {
    await renderLoaded("```ts\nconst answer = 42;\n```\n\n| 列 A | 列 B |\n| --- | --- |\n| 1 | 2 |\n");

    expect(await screen.findByRole("button", { name: "复制代码" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下载文件" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "复制表格" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下载表格" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /copy|download/i })).not.toBeInTheDocument();
  });
});

describe("StreamMarkdown 渲染惰性", () => {
  it.each(Object.entries(PAYLOADS))("%s 渲染为惰性内容", async (_name, payload) => {
    await renderLoaded(`前文\n\n${payload}\n\n后文`);
    expect(collectActiveContent(document.body)).toEqual([]);
    expect(clickableElements(document.body)).toEqual([]);
  });

  it.each(Object.entries(PAYLOADS))("%s 在任意位置分两段增量渲染时每一步都是惰性内容", async (_name, payload) => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    const { rerender } = await renderLoaded("");
    const container = document.body;
    for (let cut = 1; cut < payload.length; cut += 1) {
      rerender(<StreamMarkdown content={payload.slice(0, cut)} />);
      expect(collectActiveContent(container), `cut=${cut} 前段`).toEqual([]);
      // 前段中不完整链接渲染为占位链接按钮，点击后既不打开确认框也不打开窗口。
      for (const el of container.querySelectorAll('a, button, [data-streamdown="link"]')) fireEvent.click(el);
      expect(screen.queryByRole("button", { name: "打开链接" }), `cut=${cut} 前段确认框`).not.toBeInTheDocument();
      expect(open, `cut=${cut} 前段打开窗口`).not.toHaveBeenCalled();
      rerender(<StreamMarkdown content={payload} />);
      expect(collectActiveContent(container), `cut=${cut} 全文`).toEqual([]);
      expect(clickableElements(container), `cut=${cut} 全文可点击元素`).toEqual([]);
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


  it("外链经确认后以 noreferrer 在新窗口打开", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    const { container } = await renderLoaded("[站点](https://example.com/a)");
    fireEvent.click(container.querySelector('[data-streamdown="link"]')!);
    expect(open).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "打开链接" }));
    expect(open).toHaveBeenCalledWith("https://example.com/a", "_blank", "noreferrer");
  });
});

describe("StreamMarkdown 应用内链接", () => {
  afterEach(() => {
    window.history.replaceState(null, "", "/");
    useAppStore.setState({ scrollTarget: null, playbackStart: null });
    useProjectsStore.setState({ currentProjectName: null, currentProjectData: null });
    vi.restoreAllMocks();
  });

  it("剪辑视图链接渲染为真实链接，点击在应用内跳转而不开新窗口", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    const href = "/app/projects/demo/episodes/1?view=edit&tl=tl_ab&t=12.5";
    const { container } = await renderLoaded(`[看这里](${href})`);
    const link = container.querySelector("a");
    expect(link?.getAttribute("href")).toBe(href);
    expect(link?.textContent).toBe("看这里");
    fireEvent.click(link!);
    expect(window.location.pathname + window.location.search).toBe(href);
    expect(open).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "打开链接" })).not.toBeInTheDocument();
  });

  it("不以斜杠开头的站内写法，链接地址规范成从站点根开始，新开标签页也落到同一处", async () => {
    window.history.replaceState(null, "", "/app/projects/demo/characters");
    const { container } = await renderLoaded("[看这里](app/projects/demo/episodes/1?view=edit&t=4)");
    expect(container.querySelector("a")?.getAttribute("href")).toBe("/app/projects/demo/episodes/1?view=edit&t=4");
  });

  it("带修饰键的点击保留浏览器默认行为", async () => {
    const { container } = await renderLoaded("[看这里](/app/projects/demo/episodes/1?view=edit)");
    let prevented: boolean | null = null;
    document.addEventListener(
      "click",
      (event) => {
        prevented = event.defaultPrevented;
        event.preventDefault(); // 挡掉 jsdom 的真实导航
      },
      { once: true },
    );
    fireEvent.click(container.querySelector("a")!, { ctrlKey: true });
    expect(prevented).toBe(false);
    expect(window.location.pathname).toBe("/");
  });

  it("单元链接跳到该集、选中单元并请求从起始时间播放，一次性参数不留在地址栏", async () => {
    useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: { generation_mode: "reference_video" } as never });
    const { container } = await renderLoaded("[E1U3 的问题](/app/projects/demo/episodes/2?unit=E1U3&t=4.5)");
    fireEvent.click(container.querySelector("a")!);
    expect(window.location.pathname + window.location.search).toBe("/app/projects/demo/episodes/2");
    expect(useAppStore.getState().scrollTarget).toMatchObject({ type: "reference_unit", id: "E1U3" });
    expect(useAppStore.getState().playbackStart).toMatchObject({
      resource_type: "reference_videos",
      resource_id: "E1U3",
      seconds: 4.5,
    });
  });

  it("不带时间点的单元链接只请求打开单元预览，不指定起始时间", async () => {
    useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: { generation_mode: "storyboard" } as never });
    const { container } = await renderLoaded("[问题](/app/projects/demo/episodes/1?unit=E1S02)");
    fireEvent.click(container.querySelector("a")!);
    expect(useAppStore.getState().playbackStart).toMatchObject({
      resource_type: "videos",
      resource_id: "E1S02",
      seconds: null,
    });
  });

  it("分镜图生视频项目的单元链接按分镜单元处理", async () => {
    useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: { generation_mode: "storyboard" } as never });
    const { container } = await renderLoaded("[问题](/app/projects/demo/episodes/1?unit=E1S02&t=1)");
    fireEvent.click(container.querySelector("a")!);
    expect(useAppStore.getState().scrollTarget).toMatchObject({ type: "segment", id: "E1S02" });
    expect(useAppStore.getState().playbackStart).toMatchObject({ resource_type: "videos", resource_id: "E1S02" });
  });

  it("链接指向其他项目时只跳转，不按当前项目的生成模式发聚焦与起播请求", async () => {
    useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: { generation_mode: "storyboard" } as never });
    const { container } = await renderLoaded("[问题](/app/projects/other/episodes/2?unit=E1U3&t=4.5)");
    fireEvent.click(container.querySelector("a")!);
    expect(window.location.pathname + window.location.search).toBe("/app/projects/other/episodes/2");
    expect(useAppStore.getState().scrollTarget).toBeNull();
    expect(useAppStore.getState().playbackStart).toBeNull();
  });

  it("项目名带编码时按解码后的名称与当前项目比较", async () => {
    useProjectsStore.setState({ currentProjectName: "我的项目", currentProjectData: { generation_mode: "storyboard" } as never });
    const { container } = await renderLoaded("[问题](/app/projects/%E6%88%91%E7%9A%84%E9%A1%B9%E7%9B%AE/episodes/1?unit=E1S02)");
    fireEvent.click(container.querySelector("a")!);
    expect(useAppStore.getState().scrollTarget).toMatchObject({ type: "segment", id: "E1S02" });
  });

  it("当前没有打开任何项目时只跳转", async () => {
    const { container } = await renderLoaded("[问题](/app/projects/demo/episodes/1?unit=E1S02)");
    fireEvent.click(container.querySelector("a")!);
    expect(window.location.pathname).toBe("/app/projects/demo/episodes/1");
    expect(useAppStore.getState().scrollTarget).toBeNull();
    expect(useAppStore.getState().playbackStart).toBeNull();
  });

  it.each(["//example.com/app/projects/x", "https://example.com/app/projects/x", "/api/v1/projects/x/export"])(
    "非应用内链接 %s 仍走外链确认",
    async (href) => {
      const { container } = await renderLoaded(`[站点](${href})`);
      expect(container.querySelector("a")).toBeNull();
      expect(container.querySelector('[data-streamdown="link"]')?.tagName).toBe("BUTTON");
    },
  );
});
