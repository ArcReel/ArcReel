import type { TFunction } from "i18next";
import type { Turn } from "@/types";

// ---------------------------------------------------------------------------
// cn – lightweight className concatenation utility.
// Filters out falsy values and joins the rest with spaces.
// ---------------------------------------------------------------------------

export function cn(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

// ---------------------------------------------------------------------------
// TERMINAL_SESSION_STATUSES – session statuses treated as "done" for the
// purpose of freezing running/pending indicators (subagent cards, task rows).
// ---------------------------------------------------------------------------

export const TERMINAL_SESSION_STATUSES = new Set(["completed", "error", "interrupted"]);

// ---------------------------------------------------------------------------
// composeAllTurns – merge live draft into committed turn list for rendering.
//
// 当用户中断时，被中断的 assistant 流式内容仍存在 draftTurn 中（未完成的
// 消息不会形成权威日志条目）。此时 turns 末尾是 interrupt_notice 系统
// turn——若把 draft 直接附加在末尾，渲染会变成"中断 → 助手回复"，与时间
// 顺序相反。把 draft 插到 interrupt_notice 之前，让 UI 显示成
// "助手回复 → 中断"。刷新后 draft 自然消失（服务端内存态，不入日志）。
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
// getRoleLabel – maps a turn role (user | assistant | system) to a display label.
//
// 标签是面向用户的文本，翻译由调用方的 `useTranslation("dashboard")` 传入——这个函数
// 在组件之外（纯函数）复用，自己拿不到 hook。未知 role 原样透出，只有空值才落兜底文案。
// ---------------------------------------------------------------------------

export function getRoleLabel(role: string, t: TFunction<"dashboard">): string {
  switch (role) {
    case "assistant":
      return t("chat_role_assistant");
    case "user":
      return t("chat_role_user");
    case "system":
      return t("chat_role_system");
    default:
      return role || t("chat_role_message");
  }
}
