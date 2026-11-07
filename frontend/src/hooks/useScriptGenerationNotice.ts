import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useAssistantStore } from "@/stores/assistant-store";
import { useAppStore } from "@/stores/app-store";
import type { ContentBlock, Turn } from "@/types";

/**
 * Script generation takes minutes of LLM text generation; Agent call starts must immediately report progress so users do not assume
 * freezes. This hook watches Agent session streams, showing transient toasts when script-generation tool calls start
 * (process notices without persistent review), explaining the lengthy step.
 *
 * Tool names are SDK-registered fully qualified mcp__arcreel__<id>, matching ToolCallWithResult parsing.
 * Cover script/script-planning text tools only; lengthy media generation already has task-queue HUDs.
 */
const SCRIPT_GENERATION_TOOL_NAMES = new Set([
  "mcp__arcreel__generate_episode_script",
  "mcp__arcreel__generate_script_plan",
]);

function collectBlocks(turns: Turn[], draftTurn: Turn | null): ContentBlock[] {
  const blocks: ContentBlock[] = [];
  for (const turn of turns) {
    if (turn.content) blocks.push(...turn.content);
  }
  if (draftTurn?.content) blocks.push(...draftTurn.content);
  return blocks;
}

export function useScriptGenerationNotice(): void {
  const { t } = useTranslation("dashboard");
  // Expose latest t through refs, avoiding language changes rebuilding effects and losing shown-notice records.
  const tRef = useRef(t);
  useEffect(() => {
    tRef.current = t;
  }, [t]);

  const turns = useAssistantStore((s) => s.turns);
  const draftTurn = useAssistantStore((s) => s.draftTurn);
  const sessionStatus = useAssistantStore((s) => s.sessionStatus);

  // tool_use IDs already notified, retained across session/project switches (StudioLayout persists),
  // never re-notify the same ongoing call on return or identical drafts replayed after SSE reconnects.
  const notifiedRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    // Show lengthy-step notices only for running sessions: completed/interrupted session reloads may retain historical tool_use or
    // residual drafts without results. Running-state guards exclude these stale states.
    if (sessionStatus !== "running") return;

    const blocks = collectBlocks(turns, draftTurn);

    // Main completion signals are result/is_error on tool_use blocks: frontend entry-projection backfills
    // paired tool_result entries into originating tool_use blocks (matching ToolCallWithResult rendering),
    // so completed calls have no separate tool_result blocks. Scanning only tool_result misclassifies replayed completed history
    // as ongoing, repeating lengthy-step notices on reloaded AskUserQuestion sessions whose status remains running.
    const completedToolUseIds = new Set<string>();
    for (const block of blocks) {
      if (block.type === "tool_result" && block.tool_use_id) {
        completedToolUseIds.add(block.tool_use_id);
      }
    }

    for (const block of blocks) {
      if (
        block.type === "tool_use" &&
        block.id &&
        block.name &&
        SCRIPT_GENERATION_TOOL_NAMES.has(block.name) &&
        block.result === undefined &&
        block.is_error === undefined &&
        !completedToolUseIds.has(block.id) &&
        !notifiedRef.current.has(block.id)
      ) {
        notifiedRef.current.add(block.id);
        useAppStore.getState().pushToast(tRef.current("script_generation_notice_toast"), "info");
      }
    }
  }, [turns, draftTurn, sessionStatus]);
}
