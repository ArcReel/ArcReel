import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { API } from "@/api";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { EpisodesView, ManualSplitAction, ManualSplitResponse } from "@/types";
import { errMsg } from "@/utils/async";

import { resolvePointAction, stepPoint, type ManuscriptPoint, type PointAction } from "./manual-split-model";

interface PendingConfirm {
  action: ManualSplitAction;
  title: string;
  text: string;
  episodes: number[];
}

export interface ManualSplitState {
  pending: ManuscriptPoint | null;
  action: PointAction | null;
  moving: number | null;
  title: string;
  busy: boolean;
  setTitle: (title: string) => void;
  place: (point: ManuscriptPoint) => void;
  toggleMoving: (left: number) => void;
  cancel: () => void;
  confirmPending: () => void;
  mergeWithNext: (episode: number) => void;
  clearAfter: (episode: number) => void;
  dialog: ReactNode;
}

function isTextField(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (target.tagName === "INPUT" || target.tagName === "TEXTAREA");
}

/**
 * 「分集」视图的手工切分：插入光标、←/→ 微调（Shift 一次 10 字）、Enter 确认、Esc 取消，
 * 以及波及有产物的集时由服务端成文的确认清单。`onApplied` 收到切分或拆分出的新集 ID。
 */
export function useManualSplit(
  projectName: string,
  view: EpisodesView | null,
  onApplied: (episode: number | null) => void,
): ManualSplitState {
  const { t } = useTranslation("dashboard");
  const [pending, setPending] = useState<ManuscriptPoint | null>(null);
  const [moving, setMoving] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<PendingConfirm | null>(null);

  const action = view && pending ? resolvePointAction(view, pending, moving) : null;

  const cancel = useCallback(() => {
    setPending(null);
    setMoving(null);
  }, []);

  const finish = useCallback(
    async (response: Extract<ManualSplitResponse, { status: "applied" }>) => {
      setPending(null);
      setMoving(null);
      setTitle("");
      setConfirm(null);
      useAppStore.getState().pushToast(t("manual_split_done"), "success");
      await useProjectsStore.getState().refreshProject(projectName);
      onApplied(response.episode);
    },
    [onApplied, projectName, t],
  );

  const submit = useCallback(
    async (request: ManualSplitAction, dialogTitle: string, options: { confirmEpisodes?: number[]; dryRun?: boolean }) => {
      setBusy(true);
      try {
        const response = await API.manualSplit(projectName, request, options);
        if (response.status === "confirmation_required") {
          const { restaled, retired, text } = response.impact;
          setConfirm({ action: request, title: dialogTitle, text, episodes: [...restaled, ...retired] });
        } else {
          await finish(response);
        }
      } catch (err) {
        useAppStore.getState().pushToast(t("manual_split_failed", { message: errMsg(err) }), "error");
      } finally {
        setBusy(false);
      }
    },
    [finish, projectName, t],
  );

  const requestFor = useCallback(
    (point: PointAction): { request: ManualSplitAction; dialogTitle: string } | null => {
      if (!view) return null;
      const sourceFile = view.files[point.file]?.source_file;
      if (point.kind === "cut" && sourceFile) {
        return {
          request: { action: "cut", source_file: sourceFile, end: point.end, title },
          dialogTitle: t("manual_split_cut_confirm"),
        };
      }
      if (point.kind === "split") {
        return {
          request: { action: "split", episode: point.episode, at: point.at },
          dialogTitle: t("manual_split_split_confirm"),
        };
      }
      if (point.kind === "move") {
        return {
          request: { action: "move_boundary", episode: point.left, at: point.at },
          dialogTitle: t("manual_split_move_dialog_title"),
        };
      }
      return null;
    },
    [t, title, view],
  );

  const confirmPending = useCallback(() => {
    if (busy || action === null) return;
    const built = requestFor(action);
    if (built) void submit(built.request, built.dialogTitle, {});
  }, [action, busy, requestFor, submit]);

  const place = useCallback(
    (point: ManuscriptPoint) => {
      if (!view || busy) return;
      const next = resolvePointAction(view, point, moving);
      if (next === null) {
        setPending(null);
        return;
      }
      if (next.kind === "cut" && action?.kind !== "cut") setTitle("");
      setPending(point);
    },
    [action, busy, moving, view],
  );

  const toggleMoving = useCallback((left: number) => {
    setPending(null);
    setMoving((current) => (current === left ? null : left));
  }, []);

  const mergeWithNext = useCallback(
    (episode: number) => void submit({ action: "merge_next", episode }, t("manual_split_merge"), {}),
    [submit, t],
  );
  const clearAfter = useCallback(
    (episode: number) => void submit({ action: "clear_after", episode }, t("manual_split_clear_after"), { dryRun: true }),
    [submit, t],
  );

  // 键盘接管挂在 window 上：插入光标不是可聚焦元素，滚动原文时焦点也不在原文里
  const keyHandler = useRef<(event: KeyboardEvent) => void>(() => {});
  useEffect(() => {
    keyHandler.current = (event: KeyboardEvent) => {
      if (confirm !== null) return;
      if (event.key === "Escape" && (pending !== null || moving !== null)) {
        cancel();
        return;
      }
      if (pending === null || view === null) return;
      // 别处的输入框照常输入；操作条里的标题框按 Enter 即确认
      const inToolbar = event.target instanceof HTMLElement && event.target.closest("[data-manual-split-toolbar]");
      if (isTextField(event.target) && !(inToolbar && event.key === "Enter")) return;
      if (event.key === "Enter") {
        event.preventDefault();
        confirmPending();
        return;
      }
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        const delta = (event.shiftKey ? 10 : 1) * (event.key === "ArrowLeft" ? -1 : 1);
        setPending(stepPoint(view, pending, delta, moving));
      }
    };
  });
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => keyHandler.current(event);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const dialog = (
    <ConfirmDialog
      open={confirm !== null}
      title={confirm?.title ?? ""}
      description={<span className="whitespace-pre-line">{confirm?.text}</span>}
      confirmLabel={t("manual_split_dialog_confirm")}
      tone="danger"
      loading={busy}
      onCancel={() => setConfirm(null)}
      onConfirm={() => {
        if (confirm) void submit(confirm.action, confirm.title, { confirmEpisodes: confirm.episodes });
      }}
    />
  );

  return {
    pending,
    action,
    moving,
    title,
    busy,
    setTitle,
    place,
    toggleMoving,
    cancel,
    confirmPending,
    mergeWithNext,
    clearAfter,
    dialog,
  };
}
