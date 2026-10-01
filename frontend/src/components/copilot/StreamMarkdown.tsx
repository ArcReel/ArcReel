import { useEffect, useState, type ComponentType, type MouseEvent, type ReactNode } from "react";
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
    return <div className="whitespace-pre-wrap break-words">{content || ""}</div>;
  }

  return (
    <streamdown.Component
      className="markdown-body text-sm leading-6"
      parseIncompleteMarkdown={true}
      remarkPlugins={streamdown.remarkPlugins}
      allowedTags={ALLOWED_TAGS}
      components={COMPONENTS}
    >
      {String(content || "")}
    </streamdown.Component>
  );
}
