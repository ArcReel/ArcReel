import type { TFunction } from "i18next";
import type { SessionMeta } from "@/types";
import { formatShortDateTime } from "@/utils/date-format";

/** Session display title: fall back to creation time, then the localized new-session label. */
export function sessionTitle(session: SessionMeta | undefined, t: TFunction): string {
  return session?.title || formatShortDateTime(session?.created_at) || t("dashboard:new_session");
}
