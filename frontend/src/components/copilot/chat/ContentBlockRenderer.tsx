import type { ContentBlock } from "@/types";
import { TextBlock } from "./TextBlock";
import { ToolCallWithResult } from "./ToolCallWithResult";
import { ThinkingBlock } from "./ThinkingBlock";
import { SkillContentBlock } from "./SkillContentBlock";

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

    default: {
      // Fallback: render as text
      const text = block.text || block.content || JSON.stringify(block);
      return <TextBlock key={block.id ?? `block-${index}`} text={text} />;
    }
  }
}
