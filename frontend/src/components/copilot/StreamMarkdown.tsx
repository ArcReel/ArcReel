import { useEffect, useMemo, useState, type ComponentType, type MouseEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useOpenAppLink } from "@/hooks/useOpenAppLink";
import { parseAppLink } from "@/utils/app-link";
import { voidCall } from "@/utils/async";

// ---------------------------------------------------------------------------
// StreamMarkdown – lazy-loads the Streamdown component from the `streamdown`
// package and renders markdown content.  Falls back to a plain whitespace-
// preserving <div> while the library is loading.
// ---------------------------------------------------------------------------

interface LoadedStreamdown {
  Component: ComponentType<Record<string, unknown>>;
  remarkPlugins: unknown[];
  rehypePlugins: unknown[];
}

let streamdownPromise: Promise<LoadedStreamdown | null> | null = null;

async function loadStreamdownComponent(): Promise<LoadedStreamdown | null> {
  if (streamdownPromise) return streamdownPromise;

  streamdownPromise = import("streamdown")
    .then((mod) => {
      // The named export `Streamdown` is a MemoExoticComponent
      const Comp = (mod as Record<string, unknown>).Streamdown ??
        (mod as Record<string, unknown>).default ??
        null;
      if (!Comp) return null;
      return {
        Component: Comp as ComponentType<Record<string, unknown>>,
        // 引用保持稳定：Streamdown 按引用比较插件，变了会让已渲染的块全部重算。
        remarkPlugins: [...Object.values(mod.defaultRemarkPlugins), remarkAppLinks],
        rehypePlugins: buildRehypePlugins(mod.defaultRehypePlugins),
      };
    })
    .catch((error) => {
      console.warn("Failed to load Streamdown:", error);
      return null;
    });

  return streamdownPromise;
}

// ---------------------------------------------------------------------------
// 应用内链接：Streamdown 把所有链接渲染成「确认后新窗口打开」的按钮，站内链接要改成应用内跳转。
// 它没有导出默认的链接组件可供包装，所以由 remark 插件把站内链接节点换成自定义元素 `app-link`，
// 其余链接仍走 Streamdown 自己的渲染与外链确认。
// ---------------------------------------------------------------------------

const APP_LINK_TAG = "app-link";
const ALLOWED_TAGS = { [APP_LINK_TAG]: ["href"] };

interface MdastNode {
  type: string;
  url?: string;
  data?: Record<string, unknown>;
  children?: MdastNode[];
}

function markAppLinks(node: MdastNode): void {
  const link = node.type === "link" && node.url ? parseAppLink(node.url, window.location.origin) : null;
  if (link) {
    node.data = { ...node.data, hName: APP_LINK_TAG, hProperties: { href: link.href } };
  }
  node.children?.forEach(markAppLinks);
}

const remarkAppLinks = () => markAppLinks;

function AppLink({ href, children }: { href?: string; children?: ReactNode }) {
  const open = useOpenAppLink();
  const link = href ? parseAppLink(href, window.location.origin) : null;
  if (!href || !link) return <span>{children}</span>;
  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    // 带修饰键或非左键时保留浏览器默认行为（新标签页打开等）。
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }
    event.preventDefault();
    open(link);
  };
  return (
    <a
      href={href}
      onClick={handleClick}
      data-streamdown="link"
      className="wrap-anywhere font-medium text-primary underline"
    >
      {children}
    </a>
  );
}

const COMPONENTS = { [APP_LINK_TAG]: AppLink };

// ---------------------------------------------------------------------------
// 键盘可达的横向滚动区：代码块与表格放不下时横向滚动，没有可聚焦的子元素，键盘用户要能
// 聚焦后用方向键滚动。Streamdown 把代码块 `code` 元素上的属性透传给外层的滚动容器，
// 标成可聚焦的区域；表格的滚动容器是它外层的包裹元素，属性只能落在表格上，因此只让
// 表格可聚焦（保留表格语义），聚焦后方向键滚动的是最近的滚动祖先。
// 排在默认插件之后，不会被清洗掉。区域名称作为插件参数随界面语言传入；Streamdown 外层的
// memo 不比较 rehypePlugins，所以切换语言时以语言作 key 重新挂载，已渲染的块随之换成新名称。
// ---------------------------------------------------------------------------

interface HastNode {
  type: string;
  tagName?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
}

interface ScrollRegionLabels {
  code: string;
  table: string;
}

function markScrollRegions(node: HastNode, labels: ScrollRegionLabels, parent?: HastNode): void {
  if (node.type === "element" && node.tagName === "code" && parent?.tagName === "pre") {
    node.properties = { ...node.properties, tabIndex: 0, role: "region", ariaLabel: labels.code };
  } else if (node.type === "element" && node.tagName === "table") {
    node.properties = { ...node.properties, tabIndex: 0, ariaLabel: labels.table };
  }
  node.children?.forEach((child) => markScrollRegions(child, labels, node));
}

const rehypeScrollRegions = (labels: ScrollRegionLabels) => (tree: HastNode) => markScrollRegions(tree, labels);

interface SanitizeSchema {
  tagNames?: string[];
  attributes?: Record<string, unknown>;
}

// 传入自定义 rehypePlugins 后，Streamdown 不再把 allowedTags 合进清洗白名单（只在默认插件时合并），
// 这里照它的做法补上：raw → 放行 app-link 的 sanitize → harden；滚动区标记随语言在渲染时接上。
function buildRehypePlugins(defaults: Record<string, unknown>): unknown[] {
  const { raw, sanitize, harden } = defaults;
  const [rehypeSanitize, schema] = sanitize as [unknown, SanitizeSchema];
  const appLinkSchema: SanitizeSchema = {
    ...schema,
    tagNames: [...(schema.tagNames ?? []), ...Object.keys(ALLOWED_TAGS)],
    attributes: { ...schema.attributes, ...ALLOWED_TAGS },
  };
  return [raw, [rehypeSanitize, appLinkSchema], harden];
}

// ---------------------------------------------------------------------------
// 代码块的焦点：Streamdown 的代码块主体按需加载，分块到达时整块替换先渲染的占位节点，
// 此前聚焦在代码块上的焦点会落回 body。记下聚焦的是第几个代码块，节点被替换后把焦点
// 交给同一位置的新节点；焦点已经移到别处时不抢回。
// ---------------------------------------------------------------------------

const CODE_BLOCK_BODY = '[data-streamdown="code-block-body"]';

function useKeepCodeBlockFocus(root: HTMLElement | null): void {
  useEffect(() => {
    if (!root) return;
    let focused: { node: HTMLElement; index: number } | null = null;
    const codeBlocks = () => Array.from(root.querySelectorAll<HTMLElement>(CODE_BLOCK_BODY));

    const onFocusIn = (event: FocusEvent) => {
      const target = event.target;
      focused = target instanceof HTMLElement && target.matches(CODE_BLOCK_BODY)
        ? { node: target, index: codeBlocks().indexOf(target) }
        : null;
    };
    const onFocusOut = () => {
      // 节点被移除时不算离开：替换后的恢复由下面的观察者处理
      queueMicrotask(() => {
        if (focused?.node.isConnected) focused = null;
      });
    };
    const observer = new MutationObserver(() => {
      if (!focused || focused.node.isConnected) return;
      const { index } = focused;
      focused = null;
      if (document.activeElement && document.activeElement !== document.body) return;
      codeBlocks()[index]?.focus({ preventScroll: true });
    });

    root.addEventListener("focusin", onFocusIn);
    root.addEventListener("focusout", onFocusOut);
    observer.observe(root, { childList: true, subtree: true });
    return () => {
      root.removeEventListener("focusin", onFocusIn);
      root.removeEventListener("focusout", onFocusOut);
      observer.disconnect();
    };
  }, [root]);
}

interface StreamMarkdownProps {
  content: string;
  /** compact 用于工序行展开区（子时间线、子智能体结论），与工序行同为小字号。 */
  size?: "default" | "compact";
}

export function StreamMarkdown({ content, size = "default" }: StreamMarkdownProps) {
  const { t, i18n } = useTranslation("dashboard");
  const [streamdown, setStreamdown] = useState<LoadedStreamdown | null>(null);
  const [root, setRoot] = useState<HTMLDivElement | null>(null);
  useKeepCodeBlockFocus(root);
  const codeLabel = t("chat_code_block_label");
  const tableLabel = t("chat_table_label");
  // 引用只随加载与名称变化：Streamdown 按引用比较插件，变了会让已渲染的块全部重算。
  const rehypePlugins = useMemo(
    () => streamdown && [...streamdown.rehypePlugins, [rehypeScrollRegions, { code: codeLabel, table: tableLabel }]],
    [streamdown, codeLabel, tableLabel],
  );

  // 工具条按钮与外链确认框的文字（含无障碍名称）。格式名 SVG、CSV 等不翻译，沿用 Streamdown 默认值。
  const translations = useMemo(
    () => ({
      close: t("chat_md_close"),
      copied: t("chat_md_copied"),
      copyCode: t("chat_md_copy_code"),
      copyLink: t("chat_md_copy_link"),
      copyTable: t("chat_md_copy_table"),
      copyTableAsCsv: t("chat_md_copy_table_as", { format: "CSV" }),
      copyTableAsMarkdown: t("chat_md_copy_table_as", { format: "Markdown" }),
      copyTableAsTsv: t("chat_md_copy_table_as", { format: "TSV" }),
      downloadDiagram: t("chat_md_download_diagram"),
      downloadDiagramAsMmd: t("chat_md_download_diagram_as", { format: "MMD" }),
      downloadDiagramAsPng: t("chat_md_download_diagram_as", { format: "PNG" }),
      downloadDiagramAsSvg: t("chat_md_download_diagram_as", { format: "SVG" }),
      downloadFile: t("chat_md_download_file"),
      downloadImage: t("chat_md_download_image"),
      downloadTable: t("chat_md_download_table"),
      downloadTableAsCsv: t("chat_md_download_table_as", { format: "CSV" }),
      downloadTableAsMarkdown: t("chat_md_download_table_as", { format: "Markdown" }),
      exitFullscreen: t("chat_md_exit_fullscreen"),
      viewFullscreen: t("chat_md_view_fullscreen"),
      externalLinkWarning: t("chat_md_external_link_warning"),
      openExternalLink: t("chat_md_open_external_link"),
      openLink: t("chat_md_open_link"),
      imageNotAvailable: t("chat_md_image_not_available"),
      resetView: t("chat_md_reset_view"),
      zoomIn: t("chat_md_zoom_in"),
      zoomOut: t("chat_md_zoom_out"),
    }),
    [t],
  );

  useEffect(() => {
    let mounted = true;

    voidCall(loadStreamdownComponent().then((component) => {
      if (!mounted || !component) return;
      setStreamdown(component);
    }));

    return () => {
      mounted = false;
    };
  }, []);

  if (!streamdown) {
    return <div className="whitespace-pre-wrap wrap-break-word">{content || ""}</div>;
  }

  return (
    // 只为监听焦点与节点替换，不参与排版
    <div ref={setRoot} className="contents">
      <streamdown.Component
        key={i18n.resolvedLanguage}
        className={size === "compact" ? "markdown-body text-xs leading-5" : "markdown-body text-sm leading-6"}
        parseIncompleteMarkdown={true}
        remarkPlugins={streamdown.remarkPlugins}
        rehypePlugins={rehypePlugins}
        allowedTags={ALLOWED_TAGS}
        components={COMPONENTS}
        translations={translations}
      >
        {String(content || "")}
      </streamdown.Component>
    </div>
  );
}
