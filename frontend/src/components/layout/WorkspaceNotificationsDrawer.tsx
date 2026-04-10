import { useEffect, type RefObject } from "react";
import { useTranslation } from "react-i18next";
import {
  ArrowUpRight,
  BellRing,
  CheckCheck,
  CircleAlert,
  Info,
  Sparkles,
  X,
} from "lucide-react";
import { useAppStore } from "@/stores/app-store";
import { Popover } from "@/components/ui/Popover";
import type { WorkspaceNotification } from "@/types";

interface WorkspaceNotificationsDrawerProps {
  open: boolean;
  onClose: () => void;
  anchorRef: RefObject<HTMLElement | null>;
  onNavigate: (notification: WorkspaceNotification) => void;
}

export function WorkspaceNotificationsDrawer({
  open,
  onClose,
  anchorRef,
  onNavigate,
}: WorkspaceNotificationsDrawerProps) {
  const { t } = useTranslation("dashboard");
  const workspaceNotifications = useAppStore((s) => s.workspaceNotifications);
  const markAllWorkspaceNotificationsRead = useAppStore(
    (s) => s.markAllWorkspaceNotificationsRead
  );
  const removeWorkspaceNotification = useAppStore(
    (s) => s.removeWorkspaceNotification
  );
  useEffect(() => {
    if (open) {
      markAllWorkspaceNotificationsRead();
    }
  }, [markAllWorkspaceNotificationsRead, open]);

  const unreadCount = workspaceNotifications.filter((item) => !item.read).length;

  return (
    <Popover
      open={open}
      onClose={onClose}
      anchorRef={anchorRef}
      sideOffset={10}
      width="w-[24rem]"
      backgroundColor="rgb(2 6 23 / 0.96)"
      className="overflow-hidden rounded-[1.35rem] border border-amber-200/10 bg-slate-950/96 shadow-[0_24px_80px_rgba(15,23,42,0.55)] backdrop-blur-xl"
    >
      <div className="border-b border-white/8 bg-[radial-gradient(circle_at_top_right,rgba(251,191,36,0.16),transparent_42%),radial-gradient(circle_at_top_left,rgba(56,189,248,0.12),transparent_36%)] px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2.5">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl border border-amber-300/18 bg-amber-300/10 text-amber-100 shadow-[0_12px_32px_rgba(245,158,11,0.18)]">
              <BellRing className="h-4 w-4" />
            </span>
            <span className="rounded-full border border-white/8 bg-white/4 px-2.5 py-1 text-[11px] text-slate-300">
              {t("notifications_count", { count: workspaceNotifications.length })}
            </span>
            <span className="flex items-center gap-1.5 text-[11px] text-amber-100/85">
              <Sparkles className="h-3.5 w-3.5" />
              {t("unread_count", { count: unreadCount })}
            </span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl p-1.5 text-slate-500 transition-colors hover:bg-white/6 hover:text-slate-200"
            aria-label={t("close_notification_panel")}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="max-h-[28rem] overflow-y-auto px-3 py-3">
        {workspaceNotifications.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-3 rounded-[1.1rem] border border-dashed border-white/10 bg-white/[0.03] px-6 py-12 text-center">
            <BellRing className="h-5 w-5 text-slate-500" />
            <div>
              <p className="text-sm text-slate-200">{t("no_notifications")}</p>
              <p className="mt-1 text-xs text-slate-500">
                {t("notifications_hint")}
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            {workspaceNotifications.map((item) => {
              const actionable = Boolean(item.target);
              const toneClasses = getToneClasses(item.tone, actionable);
              const ToneIcon = getToneIcon(item.tone);

              return (
                <article
                  key={item.id}
                  className={`group rounded-[1.1rem] border px-3.5 py-3 text-sm transition-all ${toneClasses}`}
                >
                  <div className="flex items-start gap-3">
                    <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-2xl border border-white/10 bg-black/15">
                      <ToneIcon className="h-4 w-4" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-[11px] uppercase tracking-[0.18em] text-slate-400">
                          {item.read ? t("read_status") : t("new_notification")}
                        </span>
                        <span className="text-[11px] text-slate-500">
                          {formatNotificationTime(item.created_at, t)}
                        </span>
                      </div>
                      <p className="mt-1.5 whitespace-pre-wrap leading-5 text-slate-100">
                        {item.text}
                      </p>
                      <div className="mt-3 flex items-center justify-between gap-2">
                        {actionable ? (
                          <button
                            type="button"
                            onClick={() => onNavigate(item)}
                            className="inline-flex items-center gap-1 rounded-full border border-sky-300/18 bg-sky-300/10 px-3 py-1 text-xs font-medium text-sky-100 transition-all hover:-translate-y-0.5 hover:border-sky-200/35 hover:bg-sky-300/14"
                          >
                            {t("view_location")}
                            <ArrowUpRight className="h-3.5 w-3.5" />
                          </button>
                        ) : (
                          <span className="text-[11px] text-slate-500">{t("notification_only")}</span>
                        )}
                        <button
                          type="button"
                          onClick={() => removeWorkspaceNotification(item.id)}
                          className="rounded-full px-2 py-1 text-[11px] text-slate-500 transition-colors hover:bg-white/6 hover:text-slate-200"
                        >
                          {t("remove_label")}
                        </button>
                      </div>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>

      {workspaceNotifications.length > 0 && (
        <div className="border-t border-white/8 px-4 py-2.5">
          <div className="flex items-center justify-between text-[11px] text-slate-500">
            <span>{t("auto_mark_read_hint")}</span>
            <span className="inline-flex items-center gap-1">
              <CheckCheck className="h-3.5 w-3.5" />
              {t("session_records")}
            </span>
          </div>
        </div>
      )}
    </Popover>
  );
}

function getToneClasses(tone: WorkspaceNotification["tone"], actionable: boolean): string {
  if (actionable) {
    return "border-sky-300/14 bg-[linear-gradient(135deg,rgba(14,165,233,0.16),rgba(15,23,42,0.62)_58%,rgba(245,158,11,0.08))] shadow-[0_12px_34px_rgba(14,165,233,0.08)]";
  }
  switch (tone) {
    case "success":
      return "border-emerald-300/12 bg-emerald-300/6";
    case "warning":
      return "border-amber-300/12 bg-amber-300/6";
    case "error":
      return "border-rose-300/12 bg-rose-300/6";
    default:
      return "border-white/8 bg-white/[0.03]";
  }
}

function getToneIcon(tone: WorkspaceNotification["tone"]) {
  switch (tone) {
    case "warning":
    case "error":
      return CircleAlert;
    case "success":
      return Sparkles;
    default:
      return Info;
  }
}

function formatNotificationTime(timestamp: number, t: (key: string, opts?: Record<string, unknown>) => string): string {
  const diff = Date.now() - timestamp;
  if (diff < 60_000) return t("just_now");
  if (diff < 3_600_000) return t("minutes_ago", { count: Math.max(1, Math.floor(diff / 60_000)) });

  const date = new Date(timestamp);
  return `${date.getHours().toString().padStart(2, "0")}:${date
    .getMinutes()
    .toString()
    .padStart(2, "0")}`;
}
