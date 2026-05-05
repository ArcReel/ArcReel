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

const PANEL_BG =
  "linear-gradient(180deg, oklch(0.21 0.012 265 / 0.96), oklch(0.18 0.010 265 / 0.96))";

const TONE_TOKENS: Record<
  WorkspaceNotification["tone"],
  { color: string; soft: string; ring: string }
> = {
  success: {
    color: "var(--color-good)",
    soft: "oklch(0.30 0.10 155 / 0.18)",
    ring: "oklch(0.45 0.10 155 / 0.40)",
  },
  warning: {
    color: "oklch(0.85 0.13 75)",
    soft: "oklch(0.30 0.10 75 / 0.18)",
    ring: "oklch(0.45 0.13 75 / 0.40)",
  },
  error: {
    color: "oklch(0.85 0.10 25)",
    soft: "oklch(0.30 0.10 25 / 0.18)",
    ring: "oklch(0.45 0.18 25 / 0.40)",
  },
  info: {
    color: "var(--color-accent-2)",
    soft: "var(--color-accent-dim)",
    ring: "var(--color-accent-soft)",
  },
};

export function WorkspaceNotificationsDrawer({
  open,
  onClose,
  anchorRef,
  onNavigate,
}: WorkspaceNotificationsDrawerProps) {
  const { t } = useTranslation("dashboard");
  const workspaceNotifications = useAppStore((s) => s.workspaceNotifications);
  const markAllWorkspaceNotificationsRead = useAppStore(
    (s) => s.markAllWorkspaceNotificationsRead,
  );
  const removeWorkspaceNotification = useAppStore(
    (s) => s.removeWorkspaceNotification,
  );
  useEffect(() => {
    if (open) markAllWorkspaceNotificationsRead();
  }, [markAllWorkspaceNotificationsRead, open]);

  const unreadCount = workspaceNotifications.filter((item) => !item.read).length;

  return (
    <Popover
      open={open}
      onClose={onClose}
      anchorRef={anchorRef}
      sideOffset={8}
      width="w-[24rem]"
      backgroundColor="transparent"
      className="overflow-hidden rounded-xl"
      style={{
        background: PANEL_BG,
        border: "1px solid var(--color-hairline)",
        backdropFilter: "blur(14px)",
        WebkitBackdropFilter: "blur(14px)",
        boxShadow:
          "0 24px 60px -12px oklch(0 0 0 / 0.55), inset 0 1px 0 oklch(1 0 0 / 0.05)",
      }}
    >
      {/* Header */}
      <div
        className="relative flex items-center gap-2.5 px-4 py-3"
        style={{ borderBottom: "1px solid var(--color-hairline-soft)" }}
      >
        <span
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 h-px"
          style={{
            background:
              "linear-gradient(90deg, transparent, var(--color-accent-soft), transparent)",
          }}
        />
        <span
          aria-hidden
          className="grid h-7 w-7 place-items-center rounded-lg"
          style={{
            background:
              "linear-gradient(135deg, var(--color-accent-dim), oklch(0.76 0.09 295 / 0.05))",
            border: "1px solid var(--color-accent-soft)",
            color: "var(--color-accent-2)",
            boxShadow: "0 8px 18px -8px var(--color-accent-glow)",
          }}
        >
          <BellRing className="h-3.5 w-3.5" />
        </span>
        <div className="min-w-0">
          <div
            className="display-serif text-[14px] font-semibold tracking-tight"
            style={{ color: "var(--color-text)" }}
          >
            {t("workspace_notifications_title")}
          </div>
          <div
            className="num flex items-center gap-2 text-[10px] uppercase"
            style={{
              color: "var(--color-text-4)",
              letterSpacing: "1.0px",
            }}
          >
            <span>{t("notifications_count", { count: workspaceNotifications.length })}</span>
            {unreadCount > 0 && (
              <>
                <span style={{ color: "var(--color-hairline-strong)" }}>·</span>
                <span style={{ color: "var(--color-accent-2)" }}>
                  {t("unread_count", { count: unreadCount })}
                </span>
              </>
            )}
          </div>
        </div>
        <div className="flex-1" />
        <button
          type="button"
          onClick={onClose}
          className="focus-ring grid h-7 w-7 place-items-center rounded-md transition-colors"
          style={{ color: "var(--color-text-3)" }}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = "var(--color-text)";
            e.currentTarget.style.background = "oklch(1 0 0 / 0.05)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = "var(--color-text-3)";
            e.currentTarget.style.background = "transparent";
          }}
          aria-label={t("close_notification_panel")}
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Body */}
      <div className="max-h-[28rem] overflow-y-auto px-3 py-3">
        {workspaceNotifications.length === 0 ? (
          <div
            className="flex flex-col items-center justify-center gap-3 rounded-xl px-6 py-12 text-center"
            style={{
              border: "1px dashed var(--color-hairline)",
              background:
                "radial-gradient(400px 200px at 50% -10%, var(--color-accent-dim), transparent 60%), oklch(0.18 0.010 265 / 0.30)",
            }}
          >
            <span
              aria-hidden
              className="grid h-10 w-10 place-items-center rounded-xl"
              style={{
                background:
                  "linear-gradient(135deg, var(--color-accent-dim), oklch(0.76 0.09 295 / 0.04))",
                border: "1px solid var(--color-accent-soft)",
                color: "var(--color-accent-2)",
              }}
            >
              <BellRing className="h-4 w-4" />
            </span>
            <div className="space-y-1">
              <p
                className="display-serif text-[14px] font-semibold tracking-tight"
                style={{ color: "var(--color-text)" }}
              >
                {t("no_notifications")}
              </p>
              <p
                className="text-[11.5px] leading-[1.5]"
                style={{ color: "var(--color-text-3)" }}
              >
                {t("notifications_hint")}
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            {workspaceNotifications.map((item) => {
              const tone = TONE_TOKENS[item.tone];
              const ToneIcon = getToneIcon(item.tone);
              const actionable = Boolean(item.target);

              return (
                <article
                  key={item.id}
                  className="group rounded-xl px-3.5 py-3 text-[12px] transition-colors"
                  style={{
                    border: actionable
                      ? "1px solid var(--color-accent-soft)"
                      : `1px solid ${tone.ring}`,
                    background: actionable
                      ? "linear-gradient(135deg, var(--color-accent-dim) 0%, oklch(0.20 0.011 265 / 0.5) 60%)"
                      : tone.soft,
                    boxShadow: actionable
                      ? "inset 0 1px 0 oklch(1 0 0 / 0.04), 0 6px 18px -6px var(--color-accent-glow)"
                      : "inset 0 1px 0 oklch(1 0 0 / 0.03)",
                  }}
                >
                  <div className="flex items-start gap-3">
                    <span
                      className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg"
                      style={{
                        background: "oklch(0.16 0.010 265 / 0.6)",
                        border: `1px solid ${tone.ring}`,
                        color: tone.color,
                      }}
                    >
                      <ToneIcon className="h-3.5 w-3.5" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span
                          className="num text-[10px] uppercase"
                          style={{
                            color: item.read
                              ? "var(--color-text-4)"
                              : "var(--color-accent-2)",
                            letterSpacing: "1.0px",
                          }}
                        >
                          {item.read ? t("read_status") : t("new_notification")}
                        </span>
                        <span
                          className="num text-[10px]"
                          style={{ color: "var(--color-text-4)" }}
                        >
                          {formatNotificationTime(item.created_at, t)}
                        </span>
                      </div>
                      <p
                        className="mt-1.5 whitespace-pre-wrap leading-[1.55]"
                        style={{ color: "var(--color-text)" }}
                      >
                        {item.text}
                      </p>
                      <div className="mt-2.5 flex items-center justify-between gap-2">
                        {actionable ? (
                          <button
                            type="button"
                            onClick={() => onNavigate(item)}
                            className="focus-ring inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-[11px] font-medium transition-transform"
                            style={{
                              color: "oklch(0.14 0 0)",
                              background:
                                "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))",
                              boxShadow:
                                "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 4px 14px -4px var(--color-accent-glow), 0 0 0 1px var(--color-accent-soft)",
                            }}
                            onMouseEnter={(e) => {
                              e.currentTarget.style.transform = "translateY(-1px)";
                            }}
                            onMouseLeave={(e) => {
                              e.currentTarget.style.transform = "translateY(0)";
                            }}
                          >
                            {t("view_location")}
                            <ArrowUpRight className="h-3 w-3" />
                          </button>
                        ) : (
                          <span
                            className="text-[10.5px]"
                            style={{ color: "var(--color-text-4)" }}
                          >
                            {t("notification_only")}
                          </span>
                        )}
                        <button
                          type="button"
                          onClick={() => removeWorkspaceNotification(item.id)}
                          className="focus-ring rounded px-2 py-0.5 text-[10.5px] transition-colors"
                          style={{ color: "var(--color-text-4)" }}
                          onMouseEnter={(e) => {
                            e.currentTarget.style.color = "var(--color-text-2)";
                            e.currentTarget.style.background = "oklch(1 0 0 / 0.05)";
                          }}
                          onMouseLeave={(e) => {
                            e.currentTarget.style.color = "var(--color-text-4)";
                            e.currentTarget.style.background = "transparent";
                          }}
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
        <div
          className="flex items-center justify-between px-4 py-2 text-[10.5px]"
          style={{
            borderTop: "1px solid var(--color-hairline-soft)",
            color: "var(--color-text-4)",
          }}
        >
          <span>{t("auto_mark_read_hint")}</span>
          <span className="num inline-flex items-center gap-1">
            <CheckCheck className="h-3 w-3" />
            {t("session_records")}
          </span>
        </div>
      )}
    </Popover>
  );
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

function formatNotificationTime(
  timestamp: number,
  t: (key: string, opts?: Record<string, unknown>) => string,
): string {
  const diff = Date.now() - timestamp;
  if (diff < 60_000) return t("just_now");
  if (diff < 3_600_000)
    return t("minutes_ago", { count: Math.max(1, Math.floor(diff / 60_000)) });

  const date = new Date(timestamp);
  return `${date.getHours().toString().padStart(2, "0")}:${date
    .getMinutes()
    .toString()
    .padStart(2, "0")}`;
}
