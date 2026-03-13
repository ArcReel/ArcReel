import { useState } from "react";
import type { ContentBlock } from "@/types";
import { ImageLightbox } from "@/components/ui/ImageLightbox";
import { TextBlock } from "./TextBlock";
import { ToolCallWithResult } from "./ToolCallWithResult";
import { ThinkingBlock } from "./ThinkingBlock";
import { SkillContentBlock } from "./SkillContentBlock";
import { TaskProgressBlock } from "./TaskProgressBlock";

// ---------------------------------------------------------------------------
// ContentBlockRenderer – dispatches a single ContentBlock to the appropriate
// specialised renderer.
//
// Block types:
//   text           -> TextBlock (markdown)
//   tool_use       -> ToolCallWithResult (unified tool + result)
//   tool_result    -> inline fallback (standalone results are rare)
//   thinking       -> ThinkingBlock (collapsible)
//   skill_content  -> SkillContentBlock (collapsible markdown)
// ---------------------------------------------------------------------------

interface ContentBlockRendererProps {
  block: ContentBlock;
  index: number;
}

export function ContentBlockRenderer({ block, index }: ContentBlockRendererProps) {
  if (!block || typeof block !== "object") {
    return null;
  }

  const blockType = block.type || "text";
  if (!block.type && import.meta.env.DEV) {
    console.warn("[ContentBlockRenderer] block missing type, falling back to text:", block);
  }

  switch (blockType) {
    case "text":
      return <TextBlock key={block.id ?? `block-${index}`} text={block.text} />;

    case "tool_use":
      return (
        <ToolCallWithResult
          key={block.id ?? `block-${index}`}
          block={block}
        />
      );

    case "tool_result":
      // Standalone tool_result (should be rare -- usually attached to tool_use)
      return (
        <div
          key={block.id ?? `block-${index}`}
          className="my-1.5 rounded-lg border border-white/10 bg-ink-800/30 px-3 py-2"
        >
          <div className="text-[10px] uppercase tracking-wide text-slate-500 mb-1">
            {block.is_error ? "执行失败" : "工具结果"}
          </div>
          <pre className="text-xs text-slate-300 overflow-x-auto whitespace-pre-wrap">
            {block.content || ""}
          </pre>
        </div>
      );

    case "skill_content":
      return (
        <SkillContentBlock
          key={block.id ?? `block-${index}`}
          text={block.text}
        />
      );

    case "thinking":
      return (
        <ThinkingBlock
          key={block.id ?? `block-${index}`}
          thinking={block.thinking}
        />
      );

    case "task_progress":
      return (
        <TaskProgressBlock
          key={block.id ?? `block-${index}`}
          block={block}
        />
      );

    case "image":
      if (block.source?.data && block.source?.media_type) {
        return (
          <ChatImageBlock
            key={block.id ?? `block-${index}`}
            src={`data:${block.source.media_type};base64,${block.source.data}`}
          />
        );
      }
      return null;

    default: {
      // Fallback: render as text
      const text = block.text || block.content || JSON.stringify(block);
      return <TextBlock key={block.id ?? `block-${index}`} text={text} />;
    }
  }
}

function ChatImageBlock({ src }: Readonly<{ src: string }>) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        className="mt-1 cursor-pointer border-0 bg-transparent p-0"
        onClick={() => setOpen(true)}
        aria-label="点击放大图片"
      >
        <img
          src={src}
          alt="附件图片"
          className="max-w-full max-h-64 rounded-lg"
        />
      </button>
      {open && (
        <ImageLightbox src={src} alt="附件图片" onClose={() => setOpen(false)} />
      )}
    </>
  );
}
