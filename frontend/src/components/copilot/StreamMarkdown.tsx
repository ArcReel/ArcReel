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
        // Stable references: Streamdown compares plugins by reference; changes recompute all rendered blocks.
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
// In-app links: Streamdown renders links as confirmation/new-window buttons; replace internal links with application navigation.
// No exported default link components to wrap; remark replaces internal link nodes with custom `app-link` elements,
// while other links retain Streamdown rendering/external-link confirmation.
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
    // Modified/non-left clicks retain default browser behavior, e.g. new tabs.
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
// Keyboard-accessible horizontal scrolling: overflowing code blocks/tables lack focusable children; keyboard users need
// focus for arrow-key scrolling. Streamdown forwards `code` element attributes to outer scroll containers,
// marking them focusable regions; table scroll containers wrap tables, so attributes can only attach to tables, making only
// tables focusable while preserving table semantics; arrow keys then scroll nearest scrollable ancestors.
// Run after default plugins to survive sanitization. Region names enter plugin arguments by UI language; Streamdown outer
// memo ignores rehypePlugins, so remount using language keys to update rendered block names.
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

// Custom rehypePlugins stop Streamdown merging allowedTags into sanitization allowlists (done only with default plugins);
// mirror that here: raw → sanitize allowing app-link → harden; attach localized scroll-region markers during rendering.
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
// Code-block focus: Streamdown lazily loads code bodies, replacing initial placeholders entirely when chunks arrive;
// existing block focus then falls back to body. Remember focused block indexes and transfer focus
// to same-position replacement nodes, without reclaiming focus already moved elsewhere.
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
      // Removed nodes do not count as departure; observers below restore after replacement.
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
  /** compact applies to work-row expanded regions (sub-timelines/subagent conclusions), sharing small work-row fonts. */
  size?: "default" | "compact";
}

export function StreamMarkdown({ content, size = "default" }: StreamMarkdownProps) {
  const { t, i18n } = useTranslation("dashboard");
  const [streamdown, setStreamdown] = useState<LoadedStreamdown | null>(null);
  const [root, setRoot] = useState<HTMLDivElement | null>(null);
  useKeepCodeBlockFocus(root);
  const codeLabel = t("chat_code_block_label");
  const tableLabel = t("chat_table_label");
  // References change only with loading/names; Streamdown compares plugin references, with changes recomputing all rendered blocks.
  const rehypePlugins = useMemo(
    () => streamdown && [...streamdown.rehypePlugins, [rehypeScrollRegions, { code: codeLabel, table: tableLabel }]],
    [streamdown, codeLabel, tableLabel],
  );

  // Toolbar/external-link-confirmation copy including accessible names. Format names SVG/CSV remain untranslated Streamdown defaults.
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
    // Observe focus/node replacement only, without affecting layout.
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
