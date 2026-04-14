import type { Turn } from "@/types";

// ---------------------------------------------------------------------------
// cn – lightweight className concatenation utility.
// Filters out falsy values and joins the rest with spaces.
// ---------------------------------------------------------------------------

export function cn(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

// ---------------------------------------------------------------------------
// composeAllTurns – merge live draft into committed turn list for rendering.
//
// 当用户中断时，被中断的 assistant 流式内容仍存在 draftTurn 中（SDK 不会把
// 未完成的 assistant message 写入 transcript）。此时 turns 末尾是
// interrupt_notice 系统 turn——若把 draft 直接附加在末尾，渲染会变成
// "中断 → 助手回复"，与时间顺序相反。把 draft 插到 interrupt_notice 之前，
// 让 UI 显示成 "助手回复 → 中断"。刷新后 draft 自然消失（与 SDK 一致）。
// ---------------------------------------------------------------------------

export function composeAllTurns(turns: Turn[], draftTurn: Turn | null): Turn[] {
  if (!draftTurn) return turns;
  const last = turns.at(-1);
  const lastIsInterrupt = last?.type === "system"
    && (last.content ?? []).some((b) => b.type === "interrupt_notice");
  if (lastIsInterrupt && last) {
    return [...turns.slice(0, -1), draftTurn, last];
  }
  return [...turns, draftTurn];
}

// ---------------------------------------------------------------------------
// getRoleLabel – maps a turn role to a Chinese display label.
// ---------------------------------------------------------------------------

export function getRoleLabel(role: string): string {
  switch (role) {
    case "assistant":
      return "助手";
    case "user":
      return "你";
    case "tool":
      return "工具";
    case "tool_result":
      return "工具结果";
    case "skill_content":
      return "Skill";
    case "result":
      return "完成";
    case "system":
      return "系统";
    case "stream_event":
      return "流式更新";
    case "unknown":
      return "消息";
    default:
      return role || "消息";
  }
}
