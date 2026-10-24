import type { TFunction } from "i18next";
import type { SessionMeta } from "@/types";
import { formatShortDateTime } from "@/utils/date-format";

/** 会话的显示标题：没有标题时用创建时间，再不行就叫「新会话」。 */
export function sessionTitle(session: SessionMeta | undefined, t: TFunction): string {
  return session?.title || formatShortDateTime(session?.created_at) || t("dashboard:new_session");
}
