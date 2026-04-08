// ---------------------------------------------------------------------------
// cn – lightweight className concatenation utility.
// Filters out falsy values and joins the rest with spaces.
// ---------------------------------------------------------------------------

export function cn(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

// ---------------------------------------------------------------------------
// getRoleLabel – maps a turn role to a display label.
// ---------------------------------------------------------------------------

export function getRoleLabel(role: string): string {
  switch (role) {
    case "assistant":
      return "Assistant";
    case "user":
      return "You";
    case "tool":
      return "Tool";
    case "tool_result":
      return "Tool result";
    case "skill_content":
      return "Skill";
    case "result":
      return "Done";
    case "system":
      return "System";
    case "stream_event":
      return "Stream update";
    case "unknown":
      return "Message";
    default:
      return role || "Message";
  }
}
