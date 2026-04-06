// ---------------------------------------------------------------------------
// cn – lightweight className concatenation utility.
// Filters out falsy values and joins the rest with spaces.
// ---------------------------------------------------------------------------

export function cn(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

// ---------------------------------------------------------------------------
// getRoleLabel – maps a turn role to a Vietnamese display label.
// ---------------------------------------------------------------------------

export function getRoleLabel(role: string): string {
  switch (role) {
    case "assistant":
      return "Trợ lý";
    case "user":
      return "Bạn";
    case "tool":
      return "Công cụ";
    case "tool_result":
      return "Kết quả công cụ";
    case "skill_content":
      return "Skill";
    case "result":
      return "Hoàn thành";
    case "system":
      return "Hệ thống";
    case "stream_event":
      return "Cập nhật trực tuyến";
    case "unknown":
      return "Tin nhắn";
    default:
      return role || "Tin nhắn";
  }
}
