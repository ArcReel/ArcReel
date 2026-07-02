import { useEffect, useState, type ComponentType } from "react";
import { voidCall } from "@/utils/async";

// ---------------------------------------------------------------------------
// StreamMarkdown – lazy-loads the Streamdown component from the `streamdown`
// package and renders markdown content.  Falls back to a plain whitespace-
// preserving <div> while the library is loading.
// ---------------------------------------------------------------------------

let streamdownPromise: Promise<ComponentType<Record<string, unknown>> | null> | null =
  null;

async function loadStreamdownComponent(): Promise<ComponentType<Record<string, unknown>> | null> {
  if (streamdownPromise) return streamdownPromise;

  streamdownPromise = import("streamdown")
    .then((mod) => {
      // The named export `Streamdown` is a MemoExoticComponent
      const Comp = (mod as Record<string, unknown>).Streamdown ??
        (mod as Record<string, unknown>).default ??
        null;
      return Comp as ComponentType<Record<string, unknown>> | null;
    })
    .catch((error) => {
      console.warn("Failed to load Streamdown:", error);
      return null;
    });

  return streamdownPromise;
}

function sanitizeMarkdown(content: string): string {
  return content
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\]\(\s*<?(?:javascript|data):/gi, "](#");
}

interface StreamMarkdownProps {
  content: string;
}

export function StreamMarkdown({ content }: StreamMarkdownProps) {
  const [StreamdownComponent, setStreamdownComponent] =
    useState<ComponentType<Record<string, unknown>> | null>(null);

  useEffect(() => {
    let mounted = true;

    voidCall(loadStreamdownComponent().then((component) => {
      if (!mounted || !component) return;
      setStreamdownComponent(() => component);
    }));

    return () => {
      mounted = false;
    };
  }, []);

  const safeContent = sanitizeMarkdown(String(content || ""));

  if (!StreamdownComponent) {
    return <div className="whitespace-pre-wrap break-words">{content || ""}</div>;
  }

  return (
    <StreamdownComponent
      className="markdown-body text-sm leading-6"
      parseIncompleteMarkdown={true}
    >
      {safeContent}
    </StreamdownComponent>
  );
}
