import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useAssistantStore } from "@/stores/assistant-store";
import { useAppStore } from "@/stores/app-store";
import type { ContentBlock, Turn } from "@/types";

/**
 * 剧本生成是分钟级的 LLM 文本生成，agent 调用期间前端原本毫无反馈，用户容易误以为
 * 卡死。本 hook 监听助手会话流，识别到剧本生成类工具调用开始时弹一条瞬时 toast
 * （过程性提示，无需持久回看），告知该步骤耗时较长。
 *
 * 工具名是 SDK 注册后的全限定名 mcp__arcreel__<id>，与 ToolCallWithResult 的解析口径
 * 一致。仅覆盖剧本/规范化两类文本生成工具；分镜/视频等长耗时工具已有任务队列 HUD。
 */
const SCRIPT_GENERATION_TOOL_NAMES = new Set([
  "mcp__arcreel__generate_episode_script",
  "mcp__arcreel__normalize_drama_script",
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
  // 经 ref 暴露最新 t，避免切语言重建 effect、丢失已弹记录。
  const tRef = useRef(t);
  useEffect(() => {
    tRef.current = t;
  }, [t]);

  const turns = useAssistantStore((s) => s.turns);
  const draftTurn = useAssistantStore((s) => s.draftTurn);
  const sessionStatus = useAssistantStore((s) => s.sessionStatus);

  // 已弹过提示的 tool_use id。跨会话/项目切换持久（StudioLayout 常驻），
  // 切走再回同一进行中调用不重弹；SSE 重连重投同一 draft 也不重弹。
  const notifiedRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    // 仅在会话运行中弹"耗时"提示：已完成/已中断会话重新载入时，其历史 tool_use 或
    // 残留 draft 可能仍缺 result，运行态门槛把这类陈旧状态挡在外面。
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
        !completedToolUseIds.has(block.id) &&
        !notifiedRef.current.has(block.id)
      ) {
        notifiedRef.current.add(block.id);
        useAppStore.getState().pushToast(tRef.current("script_generation_notice_toast"), "info");
      }
    }
  }, [turns, draftTurn, sessionStatus]);
}
