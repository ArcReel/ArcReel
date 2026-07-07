import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ContentBlock } from "@/types";
import { ImageLightbox } from "@/components/ui/ImageLightbox";
import { TextBlock } from "./TextBlock";
import { ToolCallWithResult } from "./ToolCallWithResult";
import { ThinkingBlock } from "./ThinkingBlock";
import { SkillChip } from "./SkillChip";
import { SubagentCard } from "./SubagentCard";
import { TaskProgressBlock } from "./TaskProgressBlock";

// ---------------------------------------------------------------------------
// ContentBlockRenderer – dispatches a single ContentBlock to the appropriate
// specialised renderer.
//
// Block types:
//   text             -> TextBlock (markdown)
//   tool_use         -> SubagentCard (Agent/Task) / SkillChip (Skill)
//                       / ToolCallWithResult (unified tool + result)
//   tool_result      -> inline fallback (standalone results are rare)
//   thinking         -> ThinkingBlock (single line; streaming or summary)
//   skill_invocation -> SkillChip (standalone, no anchoring tool_use)
//   task_progress    -> TaskProgressBlock (in-place updated task state)
//   interrupt_notice -> inline interrupt indicator
//   question_answer  -> QuestionAnswerBlock (AskUserQuestion answer)
// ---------------------------------------------------------------------------

interface ContentBlockRendererProps {
  block: ContentBlock;
  index: number;
  /** 该块正在流式生成（draft turn 的末尾块）。 */
  streaming?: boolean;
}

export function ContentBlockRenderer({ block, index, streaming }: ContentBlockRendererProps) {
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
      // subagent 锚点（Agent/Task tool_use 或挂有子时间线）→ 单一折叠卡片
      if (block.name === "Agent" || block.name === "Task" || block.sub_turns) {
        return <SubagentCard key={block.id ?? `block-${index}`} block={block} />;
      }
      if (block.name === "Skill") {
        return (
          <SkillChip
            key={block.id ?? `block-${index}`}
            name={extractSkillName(block.input)}
            args={extractSkillArgs(block.input)}
            status={block.result === undefined ? "running" : block.is_error ? "error" : "ok"}
          />
        );
      }
      return (
        <ToolCallWithResult
          key={block.id ?? `block-${index}`}
          block={block}
        />
      );

    case "tool_result":
      // Standalone tool_result (should be rare -- usually attached to tool_use)
      return <StandaloneToolResult key={block.id ?? `block-${index}`} block={block} />;

    case "skill_invocation":
      return (
        <SkillChip
          key={block.id ?? `block-${index}`}
          name={block.skill_name}
          args={block.skill_args}
        />
      );

    case "thinking":
      return (
        <ThinkingBlock
          key={block.id ?? `block-${index}`}
          thinking={block.thinking}
          streaming={streaming}
        />
      );

    case "task_progress":
      return (
        <TaskProgressBlock
          key={block.id ?? `block-${index}`}
          block={block}
        />
      );

    case "interrupt_notice":
      return <InterruptNoticeBlock key={block.id ?? `block-${index}`} />;

    case "question_answer":
      return <QuestionAnswerBlock key={block.id ?? `block-${index}`} block={block} />;

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
      // Fallback: render as text (content may be non-string from SDK)
      const fallback = block.text
        || (typeof block.content === "string" ? block.content : null)
        || JSON.stringify(block);
      return <TextBlock key={block.id ?? `block-${index}`} text={fallback} />;
    }
  }
}

function extractSkillName(input: Record<string, unknown> | undefined): string {
  if (!input) return "";
  return (typeof input.skill === "string" && input.skill)
    || (typeof input.name === "string" && input.name)
    || "";
}

function extractSkillArgs(input: Record<string, unknown> | undefined): string {
  if (!input) return "";
  return typeof input.args === "string" ? input.args : "";
}

function StandaloneToolResult({ block }: Readonly<{ block: ContentBlock }>) {
  const { t } = useTranslation("dashboard");
  return (
    <div className="my-1.5 rounded-lg border border-white/10 bg-ink-800/30 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-slate-500 mb-1">
        {block.is_error ? t("tool_call_error_label") : t("tool_call_result_label")}
      </div>
      <pre className="text-xs text-slate-300 overflow-x-auto whitespace-pre-wrap">
        {typeof block.content === "string"
          ? block.content
          : block.content
            ? JSON.stringify(block.content, null, 2)
            : ""}
      </pre>
    </div>
  );
}

function InterruptNoticeBlock() {
  const { t } = useTranslation("dashboard");
  return (
    <div
      className="my-1 flex items-center gap-1.5 text-[11.5px]"
      style={{ color: "var(--color-warn)" }}
    >
      <span>{"■"}</span>
      <span>{t("chat_interrupt_notice")}</span>
    </div>
  );
}

// AskUserQuestion 答复：结构化答案逐条呈现（问题 → 所选选项），
// 无结构化答案时回退展示原始结果文本。
function QuestionAnswerBlock({ block }: Readonly<{ block: ContentBlock }>) {
  const { t } = useTranslation("dashboard");
  const answers =
    block.answers && Object.keys(block.answers).length > 0 ? block.answers : null;
  return (
    <div className="my-0.5">
      <div
        className="text-[10px] font-semibold uppercase tracking-wide"
        style={{ color: "var(--color-text-4)" }}
      >
        {t("chat_question_answer_label")}
      </div>
      {answers ? (
        <div className="mt-1 flex flex-col gap-1">
          {Object.entries(answers).map(([question, label]) => (
            <div key={question} className="text-[12.5px] leading-[1.5]">
              <span style={{ color: "var(--color-text-3)" }}>{question}</span>
              <span className="mx-1" style={{ color: "var(--color-text-4)" }}>
                {"→"}
              </span>
              <span className="font-medium" style={{ color: "var(--color-text)" }}>
                {label}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <div className="mt-1 text-[12px]" style={{ color: "var(--color-text-2)" }}>
          {block.text}
        </div>
      )}
    </div>
  );
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
