import { useTranslation } from "react-i18next";
import type { ContentBlock } from "@/types";
import { Marker, MarkerContent } from "@/components/ui/marker";
import { ChatImage } from "./ChatImage";
import { TextBlock } from "./TextBlock";
import { ToolCallWithResult } from "./ToolCallWithResult";
import { stringifyResult } from "./utils";
import { WorkDetail, WorkRow } from "./WorkRow";
import { TOOL_RESULT_ICON } from "./work-label";
import { ThinkingBlock } from "./ThinkingBlock";
import { SkillChip } from "./SkillChip";
import { SubagentCard } from "./SubagentCard";
import { TaskProgressBlock } from "./TaskProgressBlock";
import { AgentFailureCard } from "./AgentFailureCard";
import { CompactionMarker } from "./CompactionMarker";

// ---------------------------------------------------------------------------
// ContentBlockRenderer – dispatches a single ContentBlock to the appropriate
// specialised renderer.
//
// Block types:
//   text             -> TextBlock (markdown)
//   tool_use         -> SubagentCard (Agent/Task) / SkillChip (Skill)
//                       / ToolCallWithResult, all three single-line work rows (WorkRow)
//   tool_result      -> StandaloneToolResult (rare orphaned results)
//   thinking         -> ThinkingBlock (single line; streaming or summary)
//   skill_invocation -> SkillChip (standalone, no anchoring tool_use)
//   task_progress    -> TaskProgressBlock (in-place updated task state)
//   interrupt_notice -> Stopped; this reply was incomplete separator
//   question_answer  -> QuestionAnswerBlock (AskUserQuestion answer)
//   agent_failure    -> AgentFailureCard (one-sentence conclusions, raw details collapsed)
//   compact_summary  -> CompactionMarker (Context compacted separators, expandable summaries)
//   image            -> ChatImage (open originals)
// ---------------------------------------------------------------------------

interface ContentBlockRendererProps {
  block: ContentBlock;
  index: number;
  /** Blocks currently streaming (final draft-turn blocks). */
  streaming?: boolean;
  /** Blocks newly arriving during viewing; failure cards announce accordingly. */
  announce?: boolean;
}

export function ContentBlockRenderer({ block, index, streaming, announce }: ContentBlockRendererProps) {
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
      // subagent anchors (Agent/Task tool_use or attached sub-timelines) → single collapsible cards.
      if (block.name === "Agent" || block.name === "Task" || block.sub_turns) {
        return <SubagentCard key={block.id ?? `block-${index}`} block={block} />;
      }
      if (block.name === "Skill") {
        return (
          <SkillChip
            key={block.id ?? `block-${index}`}
            name={extractSkillName(block.input)}
            args={extractSkillArgs(block.input)}
            pending={block.result === undefined}
            failed={block.is_error}
            result={block.result}
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

    case "agent_failure":
      return block.failure ? <AgentFailureCard failure={block.failure} announce={announce} /> : null;

    case "compact_summary":
      return <CompactionMarker key={block.id ?? `block-${index}`} summary={block.text} />;

    case "image":
      if (block.source?.data && block.source?.media_type) {
        return (
          <ChatImage
            key={block.id ?? `block-${index}`}
            src={`data:${block.source.media_type};base64,${block.source.data}`}
            index={index + 1}
            variant="inline"
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

// Rare orphaned tool results without matching tool_use also use work rows, expanding raw results.
function StandaloneToolResult({ block }: Readonly<{ block: ContentBlock }>) {
  const { t } = useTranslation("dashboard");
  const result = stringifyResult(block.content);
  const firstLine = result.split("\n").find((line) => line.trim())?.trim() ?? "";
  return (
    <WorkRow
      icon={TOOL_RESULT_ICON}
      name={t("work_tool_result")}
      summary={firstLine}
      status={block.is_error ? "error" : "ok"}
    >
      {result && <WorkDetail label={t("tool_call_result_label")} text={result} error={block.is_error} />}
    </WorkRow>
  );
}

function InterruptNoticeBlock() {
  const { t } = useTranslation("dashboard");
  return (
    <Marker variant="separator">
      <MarkerContent>{t("chat_interrupt_notice")}</MarkerContent>
    </Marker>
  );
}

// AskUserQuestion replies: show structured answers individually (questions/selected options),
// falling back to raw result text without structured answers.
function QuestionAnswerBlock({ block }: Readonly<{ block: ContentBlock }>) {
  const answers =
    block.answers && Object.keys(block.answers).length > 0 ? block.answers : null;
  if (!answers) return <p className="whitespace-pre-wrap">{block.text}</p>;
  return (
    <dl className="flex flex-col gap-1.5">
      {Object.entries(answers).map(([question, label]) => (
        <div key={question} className="min-w-0">
          <dt className="text-muted-foreground">{question}</dt>
          <dd className="font-medium">{label}</dd>
        </div>
      ))}
    </dl>
  );
}
