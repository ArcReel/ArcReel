import { useId, useMemo, useState } from "react";
import { Check, Loader2, SearchIcon, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { cn } from "cn";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogBody,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { useAssistantStore } from "@/stores/assistant-store";
import type { SessionMeta } from "@/types";
import { formatRelativeTime, formatShortDateTime, isJustNow } from "@/utils/date-format";

/** 会话的显示标题：没有标题时用创建时间，再不行就叫「新会话」。 */
export function sessionTitle(session: SessionMeta | undefined, t: TFunction): string {
  return session?.title || formatShortDateTime(session?.created_at) || t("dashboard:new_session");
}

function SessionStatusDot({ status }: { status: SessionMeta["status"] }) {
  const { t } = useTranslation("dashboard");
  const label =
    status === "running"
      ? t("session_status_running")
      : status === "error"
        ? t("session_status_error")
        : status === "interrupted"
          ? t("session_status_interrupted")
          : status === "completed"
            ? t("session_status_completed")
            : t("session_status_idle");
  return (
    <span
      role="img"
      aria-label={label}
      className={cn(
        "size-1.5 shrink-0 rounded-full",
        status === "running" && "animate-breath bg-primary",
        status === "error" && "bg-destructive",
        status !== "running" && status !== "error" && "bg-muted-foreground/40",
      )}
    />
  );
}

interface SessionHistoryProps {
  onPick: (sessionId: string) => void;
  onDelete: (sessionId: string) => Promise<void>;
  /** 收起历史视图，回到消息区。 */
  onClose: () => void;
}

// ---------------------------------------------------------------------------
// SessionHistory — 「历史」开关打开后替换消息区的会话列表。
// 可按标题搜索；行上显示状态点与相对时间，删除在行尾，确认后不可恢复。
// ---------------------------------------------------------------------------

export function SessionHistory({ onPick, onDelete, onClose }: SessionHistoryProps) {
  const { t, i18n } = useTranslation(["dashboard", "common"]);
  const sessions = useAssistantStore((s) => s.sessions);
  const currentSessionId = useAssistantStore((s) => s.currentSessionId);
  const [query, setQuery] = useState("");
  const [toDelete, setToDelete] = useState<SessionMeta | null>(null);
  const headingId = useId();

  const matched = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return sessions;
    return sessions.filter((session) => sessionTitle(session, t).toLowerCase().includes(q));
  }, [query, sessions, t]);

  const relativeTime = (iso: string) =>
    isJustNow(iso) ? t("dashboard:session_updated_just_now") : (formatRelativeTime(iso, i18n.language) ?? "");

  return (
    // 只接住从搜索框与列表行冒泡上来的 Esc，区域本身不可交互
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- 见上一行
    <section
      aria-labelledby={headingId}
      className="flex min-h-0 flex-1 flex-col"
      onKeyDown={(event) => {
        // Esc 先回到消息区；阻止默认行为，外壳不会因这次 Esc 收起面板。
        // 删除确认框经 Portal 渲染，它的 Esc 在 React 树里也会冒泡到这里，只处理本视图 DOM 内的按键
        if (
          event.key === "Escape" &&
          !event.defaultPrevented &&
          event.currentTarget.contains(event.target as Node)
        ) {
          event.preventDefault();
          onClose();
        }
      }}
    >
      <div className="flex shrink-0 flex-col gap-2 px-3 pt-3 pb-2">
        <h2 id={headingId} className="text-xs font-medium text-muted-foreground">
          {t("dashboard:session_history_title")}
        </h2>
        <InputGroup>
          <InputGroupInput
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("dashboard:session_search_placeholder")}
            aria-label={t("dashboard:session_search_placeholder")}
          />
          <InputGroupAddon>
            <SearchIcon aria-hidden />
          </InputGroupAddon>
        </InputGroup>
      </div>

      <div className="relative min-h-0 flex-1 overflow-y-auto px-2 pb-3 [scrollbar-gutter:stable]">
        {matched.length === 0 ? (
          <p role="status" className="px-2 py-8 text-center text-sm text-muted-foreground">
            {sessions.length === 0 ? t("dashboard:session_history_empty") : t("dashboard:session_search_no_match")}
          </p>
        ) : (
          <ul className="flex flex-col gap-0.5">
            {matched.map((session) => {
              const title = sessionTitle(session, t);
              const current = session.id === currentSessionId;
              return (
                <li key={session.id} className="group/session flex min-w-0 items-center gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="min-w-0 flex-1 justify-start"
                    aria-current={current ? "true" : undefined}
                    onClick={() => onPick(session.id)}
                  >
                    <SessionStatusDot status={session.status} />
                    <TruncatedText
                      text={title}
                      focusable={false}
                      className={cn("flex-1 text-left", current ? "text-foreground" : "font-normal text-subtle-foreground")}
                    />
                    {current && <Check aria-hidden className="text-muted-foreground" />}
                    <span className="shrink-0 text-xs font-normal text-muted-foreground">{relativeTime(session.updated_at)}</span>
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={t("dashboard:delete_session_named", { title })}
                    onClick={() => setToDelete(session)}
                  >
                    <Trash2 aria-hidden />
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <DeleteSessionDialog session={toDelete} onDelete={onDelete} onClose={() => setToDelete(null)} />
    </section>
  );
}

function DeleteSessionDialog({
  session,
  onDelete,
  onClose,
}: {
  session: SessionMeta | null;
  onDelete: (sessionId: string) => Promise<void>;
  onClose: () => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  // 关闭动画期间保留上一次的会话，标题不闪成空白
  const [shown, setShown] = useState(session);
  if (session !== null && session !== shown) setShown(session);
  const [deleting, setDeleting] = useState(false);
  const [failed, setFailed] = useState(false);

  const close = () => {
    setFailed(false);
    onClose();
  };

  const confirm = async () => {
    if (!session || deleting) return;
    setDeleting(true);
    setFailed(false);
    try {
      await onDelete(session.id);
    } finally {
      setDeleting(false);
    }
    // 删除接口失败时会话仍在列表里：留在对话框里说明，用户可以重试
    if (useAssistantStore.getState().sessions.some((item) => item.id === session.id)) {
      setFailed(true);
      return;
    }
    close();
  };

  const title = sessionTitle(shown ?? undefined, t);

  return (
    <AlertDialog
      open={session !== null}
      onOpenChange={(open) => {
        // 删除中不响应 Esc 与遮罩点击，请求落定前对话框不消失
        if (!open && !deleting) close();
      }}
    >
      <AlertDialogContent size="sm">
        <AlertDialogHeader>
          <AlertDialogTitle>{t("dashboard:delete_session_title", { title })}</AlertDialogTitle>
          <AlertDialogDescription>{t("dashboard:delete_session_desc")}</AlertDialogDescription>
        </AlertDialogHeader>
        {failed && (
          // 失败说明放进唯一的滚动区：窗口很矮时不会被头尾夹住裁掉
          <AlertDialogBody>
            <p role="alert" className="text-destructive">
              {t("dashboard:delete_session_failed")}
            </p>
          </AlertDialogBody>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={deleting}>{t("common:cancel")}</AlertDialogCancel>
          <AlertDialogAction variant="destructive" disabled={deleting} onClick={() => void confirm()}>
            {deleting && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
            {t("dashboard:delete_session")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
