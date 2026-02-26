// ---------------------------------------------------------------------------
// cn – lightweight className concatenation utility.
// Filters out falsy values and joins the rest with spaces.
// ---------------------------------------------------------------------------

export function cn(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
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
