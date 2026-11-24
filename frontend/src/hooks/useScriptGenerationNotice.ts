import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useAssistantStore } from "@/stores/assistant-store";
import { useAppStore } from "@/stores/app-store";
import type { ContentBlock, Turn } from "@/types";

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
  const tRef = useRef(t);
  useEffect(() => {
    tRef.current = t;
  }, [t]);

  const turns = useAssistantStore((s) => s.turns);
  const draftTurn = useAssistantStore((s) => s.draftTurn);
  const sessionStatus = useAssistantStore((s) => s.sessionStatus);

  const notifiedRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (sessionStatus !== "running") return;

    const blocks = collectBlocks(turns, draftTurn);

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
