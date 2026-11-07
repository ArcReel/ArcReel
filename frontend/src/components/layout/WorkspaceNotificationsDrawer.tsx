import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowUpRight, Bell, BellRing, CircleAlert, CircleCheck, Info, X } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Popover, PopoverContent, PopoverTitle, PopoverTrigger } from "@/components/ui/popover";
import { useNowTick } from "@/hooks/useNowTick";
import { useAppStore } from "@/stores/app-store";
import type { WorkspaceNotification } from "@/types";

interface WorkspaceNotificationsProps {
  /** View and locate action: caller handles navigation and scroll positioning, then the panel closes. */
  onNavigate: (notification: WorkspaceNotification) => void;
}

const TONE_ICON = {
  success: { icon: CircleCheck, className: "text-good" },
  warning: { icon: CircleAlert, className: "text-warn" },
  error: { icon: CircleAlert, className: "text-destructive" },
  info: { icon: Info, className: "text-primary" },
} as const;

function unreadIds(): string[] {
  return useAppStore
    .getState()
    .workspaceNotifications.filter((item) => !item.read)
    .map((item) => item.id);
}

/**
 * Header notification bell and panel anchored below it. Opening marks all notifications read and immediately clears the badge;
 * items unread on opening, plus new arrivals while open, stay highlighted until closing.
 */
export function WorkspaceNotifications({ onNavigate }: WorkspaceNotificationsProps) {
  const { t } = useTranslation("dashboard");
  const notifications = useAppStore((s) => s.workspaceNotifications);
  const markAllRead = useAppStore((s) => s.markAllWorkspaceNotificationsRead);
  const remove = useAppStore((s) => s.removeWorkspaceNotification);
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState<ReadonlySet<string>>(() => new Set());

  const handleOpenChange = (next: boolean) => {
    setOpen(next);
    if (next) {
      setHighlighted(new Set(unreadIds()));
      markAllRead();
    } else {
      setHighlighted(new Set());
    }
  };

  // New arrivals while open also count as read immediately, retaining highlight until closing
  useEffect(() => {
    if (!open) return;
    return useAppStore.subscribe((state, prev) => {
      if (state.workspaceNotifications === prev.workspaceNotifications) return;
      const arrived = unreadIds();
      if (arrived.length === 0) return;
      setHighlighted((current) => new Set([...current, ...arrived]));
      markAllRead();
    });
  }, [open, markAllRead]);

  const unreadCount = notifications.filter((item) => !item.read).length;

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger
        render={
          <Button
            variant="ghost"
            size="icon"
            className="relative"
            aria-label={
              unreadCount > 0
                ? t("notifications_trigger_unread", { count: unreadCount })
                : t("workspace_notifications_title")
            }
          />
        }
      >
        <Bell aria-hidden />
        {unreadCount > 0 && (
          <span
            aria-hidden
            className="absolute -top-1 -right-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-xs font-medium text-primary-foreground"
          >
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        )}
      </PopoverTrigger>
      <PopoverContent align="end" className="w-96">
        <div className="flex shrink-0 items-baseline gap-2 px-1">
          <PopoverTitle className="min-w-0 flex-1">{t("workspace_notifications_title")}</PopoverTitle>
          {notifications.length > 0 && (
            <span className="text-xs text-muted-foreground">
              {t("notifications_count", { count: notifications.length })}
            </span>
          )}
        </div>
        {notifications.length === 0 ? (
          <Empty>
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <BellRing aria-hidden />
              </EmptyMedia>
              <EmptyTitle>{t("no_notifications")}</EmptyTitle>
              <EmptyDescription>{t("notifications_hint")}</EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <ul className="relative -mx-1 flex min-h-0 flex-col gap-0.5 overflow-y-auto">
            {notifications.map((item) => (
              <NotificationRow
                key={item.id}
                item={item}
                highlighted={highlighted.has(item.id)}
                onNavigate={() => {
                  handleOpenChange(false);
                  onNavigate(item);
                }}
                onRemove={() => remove(item.id)}
              />
            ))}
          </ul>
        )}
        {notifications.length > 0 && (
          <p className="shrink-0 border-t border-border px-1 pt-2 text-xs text-muted-foreground">
            {t("notifications_session_hint")}
          </p>
        )}
      </PopoverContent>
    </Popover>
  );
}

function NotificationRow({
  item,
  highlighted,
  onNavigate,
  onRemove,
}: {
  item: WorkspaceNotification;
  highlighted: boolean;
  onNavigate: () => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation("dashboard");
  const tone = TONE_ICON[item.tone];
  const ToneIcon = tone.icon;
  return (
    <li className={cn("flex gap-2.5 rounded-md py-2 pr-1 pl-2.5", highlighted && "bg-primary/10")}>
      <ToneIcon aria-hidden className={cn("mt-0.5 size-4 shrink-0", tone.className)} />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <p className="text-sm break-words whitespace-pre-wrap">
          {highlighted && <span className="sr-only">{t("new_notification")}：</span>}
          {item.text}
        </p>
        <div className="flex items-center gap-2">
          <NotificationAge timestamp={item.created_at} />
          {item.target && (
            <Button variant="link" size="xs" onClick={onNavigate}>
              {t("view_location")}
              <ArrowUpRight aria-hidden data-icon="inline-end" />
            </Button>
          )}
        </div>
      </div>
      <Button variant="ghost" size="icon-xs" aria-label={t("remove_notification")} onClick={onRemove}>
        <X aria-hidden />
      </Button>
    </li>
  );
}

/**
 * Notification age, extracted into a component so the shared clock runs only while the panel is open and entries are mounted.
 */
function NotificationAge({ timestamp }: { timestamp: number }) {
  const { t } = useTranslation("dashboard");
  const now = useNowTick();
  return <span className="num text-xs text-subtle-foreground">{formatNotificationTime(timestamp, now, t)}</span>;
}

function formatNotificationTime(
  timestamp: number,
  now: number,
  t: (key: string, opts?: Record<string, unknown>) => string,
): string {
  const diff = now - timestamp;
  if (diff < 60_000) return t("just_now");
  if (diff < 3_600_000) return t("minutes_ago", { count: Math.max(1, Math.floor(diff / 60_000)) });

  const date = new Date(timestamp);
  return `${date.getHours().toString().padStart(2, "0")}:${date.getMinutes().toString().padStart(2, "0")}`;
}
