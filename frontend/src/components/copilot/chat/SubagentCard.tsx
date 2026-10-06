import { useTranslation } from "react-i18next";
import type { ContentBlock } from "@/types";
import { useArrivedFailures } from "./arrived-failures";
import { ContentBlockRenderer } from "./ContentBlockRenderer";
import { TextBlock } from "./TextBlock";
import { WorkRow, useSessionDone } from "./WorkRow";
import { SUBAGENT_ICON, segmentWorkBlocks, type WorkStatus } from "./work-label";

// ---------------------------------------------------------------------------
// SubagentCard – 子智能体（Agent/Task tool_use）的工序行，默认收起：描述加运行中的用量。
// 展开后是子时间线（按 parent_tool_use_id 归组的内部消息，工序同样是单行工序），
// 末尾是子智能体结论。实时与历史回放走同一投影，呈现一致。终态取 task_info.task_status 的
// completed / failed / stopped；合成卡片的描述在 task_info.description，结论在 task_info.summary。
// ---------------------------------------------------------------------------

function deriveStatus(block: ContentBlock, sessionDone: boolean): WorkStatus {
  const task = block.task_info;
  if (task?.task_status === "failed" || block.is_error) return "error";
  if (task?.task_status === "completed" || block.result !== undefined) return "ok";
  // 缺锚点子智能体的推断终态：子时间线停在工具调用中途
  if (task?.task_status === "stopped") return "stopped";
  // 会话终结且子智能体无终态时显示已停止，避免运行状态悬挂。
  return sessionDone ? "stopped" : "running";
}

function deriveDescription(block: ContentBlock): string {
  const input = block.input ?? {};
  const fromInput = typeof input.description === "string" ? input.description : "";
  const fromTask = block.task_info?.description ?? "";
  const fromPrompt = typeof input.prompt === "string" ? input.prompt : "";
  return fromInput || fromTask || fromPrompt;
}

export function SubagentCard({ block }: { block: ContentBlock }) {
  const { t } = useTranslation("dashboard");
  const sessionDone = useSessionDone();
  const status = deriveStatus(block, sessionDone);

  const arrived = useArrivedFailures();
  const subTurns = block.sub_turns ?? [];
  const subBlocks = subTurns.flatMap((turn) => (Array.isArray(turn.content) ? turn.content : []));
  // 查看期间新到达的子时间线失败播报；展开卡片看到的既有失败不播报
  const announced = new Set(
    subTurns.flatMap((turn) =>
      turn.uuid !== undefined && arrived.has(turn.uuid) && Array.isArray(turn.content) ? turn.content : [],
    ),
  );
  // 合成卡片（压缩续接后缺锚点的子智能体）没有 result，结论在 task_info.summary
  const conclusion = (typeof block.result === "string" ? block.result : (block.task_info?.summary ?? "")).trim();
  const tokens = status === "running" ? block.task_info?.usage?.total_tokens : undefined;
  const summary = [deriveDescription(block), tokens != null ? t("subagent_tokens", { count: tokens }) : ""]
    .filter(Boolean)
    .join(" · ");

  return (
    <WorkRow icon={SUBAGENT_ICON} name={t("subagent_card_label")} summary={summary} status={status}>
      {(subBlocks.length > 0 || conclusion) && (
        <>
          {subBlocks.length > 0 && <SubTimeline blocks={subBlocks} announced={announced} />}
          {conclusion && (
            <div className="flex min-w-0 flex-col gap-0.5">
              <p className="text-xs text-muted-foreground">{t("work_subagent_conclusion")}</p>
              <div className="text-xs text-subtle-foreground">
                <TextBlock text={conclusion} size="compact" />
              </div>
            </div>
          )}
        </>
      )}
    </WorkRow>
  );
}

/** 子时间线：内部消息的正文与工序依次排列，连续的工序排成紧凑的一列。 */
function SubTimeline({ blocks, announced }: { blocks: ContentBlock[]; announced: ReadonlySet<ContentBlock> }) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5 text-xs text-subtle-foreground">
      {segmentWorkBlocks(blocks).map(({ work, items }) =>
        work ? (
          <div key={items[0].index} className="flex min-w-0 flex-col">
            {items.map(({ block, index }) => (
              <ContentBlockRenderer key={block.id ?? index} block={block} index={index} announce={announced.has(block)} />
            ))}
          </div>
        ) : items[0].block.type === "text" ? (
          <TextBlock key={items[0].block.id ?? items[0].index} text={items[0].block.text} size="compact" />
        ) : (
          <ContentBlockRenderer
            key={items[0].block.id ?? items[0].index}
            block={items[0].block}
            index={items[0].index}
            announce={announced.has(items[0].block)}
          />
        ),
      )}
    </div>
  );
}
