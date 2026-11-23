import { useTranslation } from "react-i18next";
import type { ContentBlock } from "@/types";
import { useArrivedFailures } from "./arrived-failures";
import { ContentBlockRenderer } from "./ContentBlockRenderer";
import { TextBlock } from "./TextBlock";
import { WorkRow, useSessionDone } from "./WorkRow";
import { SUBAGENT_ICON, segmentWorkBlocks, type WorkStatus } from "./work-label";


function deriveStatus(block: ContentBlock, sessionDone: boolean): WorkStatus {
  const task = block.task_info;
  if (task?.task_status === "failed" || block.is_error) return "error";
  if (task?.task_status === "completed" || block.result !== undefined) return "ok";
  if (task?.task_status === "stopped") return "stopped";
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
  const announced = new Set(
    subTurns.flatMap((turn) =>
      turn.uuid !== undefined && arrived.has(turn.uuid) && Array.isArray(turn.content) ? turn.content : [],
    ),
  );
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
