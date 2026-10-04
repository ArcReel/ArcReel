import type { ContentBlock, Turn } from "@/types";
import { composeAllTurns } from "./utils";

// ---------------------------------------------------------------------------
// 消息区的显示层：把投影出的 turn 列表整理成逐条渲染的显示项。
//
// 投影按 SDK 消息切分，一次回复常被拆成多条 assistant turn，其中不少只含签名的空思考块；
// 流式草稿也是单独的一条。显示层把两条用户消息之间连续的 assistant turn 合成一轮，
// 工序与复制都以整轮为单位，并跳过没有可见内容的 turn，不为它们渲染空项与多余间距。
// ---------------------------------------------------------------------------

export interface DisplayItem {
  /** 渲染与滚动定位用的身份。一轮回复的身份取自它前面那一项，流式草稿落库前后保持不变。 */
  key: string;
  /** 合并后的 turn：一轮回复的内容块按时间顺序拼接，时间取最后一段。 */
  turn: Turn;
  /** 这一项包含流式草稿，末尾块仍在生成。 */
  streaming: boolean;
}

export function buildDisplayItems(turns: Turn[], draftTurn: Turn | null): DisplayItem[] {
  const items: DisplayItem[] = [];
  composeAllTurns(turns, draftTurn).forEach((turn, index) => {
    const streaming = turn === draftTurn;
    if (!streaming && !hasVisibleContent(turn)) return;

    const previous = items.at(-1);
    if (turn.type === "assistant" && previous?.turn.type === "assistant") {
      previous.turn = {
        ...previous.turn,
        content: [...previous.turn.content, ...turn.content],
        timestamp: turn.timestamp ?? previous.turn.timestamp,
      };
      previous.streaming ||= streaming;
      return;
    }

    // 一轮回复跟在用户消息或系统事件之后，以前一项为锚命名：草稿落库后首条 turn 换成
    // 条目 uuid，身份若跟着变，整轮会重新挂载，展开中的工序随之收起
    const key = turn.type === "assistant"
      ? `${previous?.key ?? "start"}:reply`
      : (turn.uuid ?? `turn-${index}`);
    items.push({ key, turn, streaming });
  });
  return items;
}

function hasVisibleContent(turn: Turn): boolean {
  return (turn.content ?? []).some(isVisibleBlock);
}

function isVisibleBlock(block: ContentBlock): boolean {
  switch (block.type) {
    case "text":
      return Boolean(block.text?.trim());
    case "thinking":
      return Boolean(block.thinking?.trim());
    case "image":
      return Boolean(block.source?.data);
    case "agent_failure":
      return Boolean(block.failure);
    default:
      return true;
  }
}
