/**
 * Agent 凭证 base_url 的归一与校验，与后端 lib/config/url_utils.py 逐字对应。
 *
 * 「存储值即调用值」：Claude CLI 内置 SDK 固定在 base_url 后拼 /v1/messages，
 * 前端预览的地址必须与后端探测、运行时请求是同一个字符串。
 */

/** Claude CLI 固定追加的路径段。 */
export const MESSAGES_PATH = "/v1/messages";

/**
 * 去首尾空白、去尾部斜杠；末尾恰好是 /v1/messages 时去掉这一段。
 *
 * 不剥 /vN、不剥单独的 /messages，也不识别或追加 /anthropic 类子路径。
 */
export function normalizeAnthropicBaseUrl(raw: string): string {
  const trimmed = raw.trim().replace(/\/+$/, "");
  // 剥掉端点后再去一次尾斜杠：https://x//v1/messages 的存储值须是 https://x。
  return trimmed.endsWith(MESSAGES_PATH)
    ? trimmed.slice(0, -MESSAGES_PATH.length).replace(/\/+$/, "")
    : trimmed;
}

/** 归一后的调用地址预览；与后端 messages_url 同值。 */
export function anthropicMessagesUrl(raw: string): string {
  return normalizeAnthropicBaseUrl(raw) + MESSAGES_PATH;
}

/**
 * 是否含后端一律 422 的成分：query、fragment、userinfo。
 *
 * 其余非法形态（缺 scheme / host 等）交给后端判定，避免前端复刻一套解析规则。
 */
export function hasUnsupportedUrlComponents(raw: string): boolean {
  const value = raw.trim();
  if (!value) return false;
  if (value.includes("?") || value.includes("#")) return true;
  try {
    const parsed = new URL(value);
    return parsed.username !== "" || parsed.password !== "";
  } catch {
    return false;
  }
}
