import { useEffect, useState, type ComponentType, type MouseEvent, type ReactNode } from "react";
import i18n from "@/i18n";
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
// 排在默认插件之后，不会被清洗掉。
// ---------------------------------------------------------------------------

interface HastNode {
  type: string;
  tagName?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
}

function markScrollRegions(node: HastNode, parent?: HastNode): void {
  if (node.type === "element" && node.tagName === "code" && parent?.tagName === "pre") {
    node.properties = {
      ...node.properties,
      tabIndex: 0,
      role: "region",
      ariaLabel: i18n.t("dashboard:chat_code_block_label"),
    };
  } else if (node.type === "element" && node.tagName === "table") {
    node.properties = { ...node.properties, tabIndex: 0, ariaLabel: i18n.t("dashboard:chat_table_label") };
  }
  node.children?.forEach((child) => markScrollRegions(child, node));
}

const rehypeScrollRegions = () => (tree: HastNode) => markScrollRegions(tree);

interface SanitizeSchema {
  tagNames?: string[];
  attributes?: Record<string, unknown>;
}

// 传入自定义 rehypePlugins 后，Streamdown 不再把 allowedTags 合进清洗白名单（只在默认插件时合并），
// 这里照它的做法补上：raw → 放行 app-link 的 sanitize → harden，再接滚动区标记。
function buildRehypePlugins(defaults: Record<string, unknown>): unknown[] {
  const { raw, sanitize, harden } = defaults;
  const [rehypeSanitize, schema] = sanitize as [unknown, SanitizeSchema];
  const appLinkSchema: SanitizeSchema = {
    ...schema,
    tagNames: [...(schema.tagNames ?? []), ...Object.keys(ALLOWED_TAGS)],
    attributes: { ...schema.attributes, ...ALLOWED_TAGS },
  };
  return [raw, [rehypeSanitize, appLinkSchema], harden, rehypeScrollRegions];
}

interface StreamMarkdownProps {
  content: string;
}

export function StreamMarkdown({ content }: StreamMarkdownProps) {
  const [streamdown, setStreamdown] = useState<LoadedStreamdown | null>(null);

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
    <streamdown.Component
      className="markdown-body text-sm leading-6"
      parseIncompleteMarkdown={true}
      remarkPlugins={streamdown.remarkPlugins}
      rehypePlugins={streamdown.rehypePlugins}
      allowedTags={ALLOWED_TAGS}
      components={COMPONENTS}
    >
      {String(content || "")}
    </streamdown.Component>
  );
}
