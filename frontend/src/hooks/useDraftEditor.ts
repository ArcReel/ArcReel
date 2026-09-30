import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { API, ApiRequestError } from "@/api";
import type { SaveEpisodeDraftResult, ScriptReviewQuarantine } from "@/types";
import { useAppStore } from "@/stores/app-store";

interface DraftEditorOptions<T> {
  projectName: string;
  episode: number;
  /** 服务端的草稿视图（待修复草稿）；为 null 时本 hook 不持有任何编辑。 */
  view: ScriptReviewQuarantine | null;
  /**
   * 把草稿正文收窄成面板可编辑的形状；收不成（结构已损坏）时返回 null，面板改呈只读说明。
   * 须是稳定引用（模块级函数或 `useCallback`）。
   */
  narrow: (content: Record<string, unknown> | null) => T | null;
  /** 保存或丢弃落定后通知调用方刷新自己的服务端态（采用后正式内容已变）。 */
  onSettled: () => void;
}

interface Synced<T> {
  revision: string | null;
  base: T | null;
  edited: T | null;
}

export interface DraftEditorHandle<T> {
  /** 当前编辑中的草稿正文；草稿结构收不成可编辑形状时为 null。 */
  content: T | null;
  setContent: (update: (prev: T) => T) => void;
  dirty: boolean;
  /** 服务端草稿已被他方改过、而本地有未保存编辑：保存会被拒绝，可放弃本地编辑载入最新。 */
  outdated: boolean;
  saving: boolean;
  discarding: boolean;
  /** 保存并校验；违约清零即采用。 */
  save: () => Promise<void>;
  /** 丢弃草稿；返回是否成功，供调用方决定是否收起确认框。 */
  discard: () => Promise<boolean>;
  /** 放弃本地编辑，载入服务端最新草稿。 */
  reloadLatest: () => void;
}

function clone<T>(value: T | null): T | null {
  return value == null ? null : (JSON.parse(JSON.stringify(value)) as T);
}

function isDirty<T>(synced: Synced<T>): boolean {
  return JSON.stringify(synced.edited) !== JSON.stringify(synced.base);
}

function diagnosticCode(err: unknown): string | null {
  if (!(err instanceof ApiRequestError)) return null;
  const diagnostic = err.diagnostic;
  if (diagnostic == null || typeof diagnostic !== "object") return null;
  const code = (diagnostic as { code?: unknown }).code;
  return typeof code === "string" ? code : null;
}

/**
 * 待修复草稿的就地手修：本地编辑、保存并校验（违约清零即采用）、丢弃。
 *
 * 服务端草稿变了（Agent 写入、另一处保存）而本地没有未保存编辑时，直接采用新的草稿；有未保存编辑时
 * 保留编辑并标记 `outdated`，保存会以并发冲突被拒，届时载入最新草稿。
 */
export function useDraftEditor<T>({ projectName, episode, view, narrow, onSettled }: DraftEditorOptions<T>): DraftEditorHandle<T> {
  const { t } = useTranslation("dashboard");
  const pushToast = useAppStore((s) => s.pushToast);
  const [synced, setSynced] = useState<Synced<T> | null>(null);
  const [saving, setSaving] = useState(false);
  const [discarding, setDiscarding] = useState(false);

  const incomingRevision = view?.revision ?? null;
  const adoptView = useCallback(
    (next: ScriptReviewQuarantine) => {
      const base = narrow(next.content);
      setSynced({ revision: next.revision, base, edited: clone(base) });
    },
    [narrow],
  );

  // 服务端草稿变化时的派生状态（render 期同步）：无未保存编辑才采用。
  if (view != null && (synced == null || (synced.revision !== incomingRevision && !isDirty(synced)))) {
    adoptView(view);
  }
  if (view == null && synced != null) {
    setSynced(null);
  }

  const current = view != null ? synced : null;
  const dirty = current != null && isDirty(current);
  const outdated = current != null && current.revision !== incomingRevision;

  const setContent = useCallback((update: (prev: T) => T) => {
    setSynced((prev) => (prev?.edited == null ? prev : { ...prev, edited: update(prev.edited) }));
  }, []);

  const reloadLatest = useCallback(() => {
    if (view != null) adoptView(view);
  }, [view, adoptView]);

  const save = useCallback(async () => {
    if (view == null || current?.edited == null) return;
    setSaving(true);
    try {
      const result: SaveEpisodeDraftResult = await API.saveEpisodeDraft(
        projectName,
        episode,
        view.doc_type,
        current.edited,
        current.revision ?? "",
      );
      if (result.adopted) {
        pushToast(t("draft_adopted_toast"), "success");
      } else if (result.draft != null) {
        adoptView(result.draft);
        pushToast(t("draft_saved_with_violations_toast", { count: result.draft.violations.length }), "warning");
      }
      onSettled();
    } catch (err) {
      if (diagnosticCode(err) === "revision_conflict") {
        adoptView(view);
        pushToast(t("draft_conflict_toast"), "warning");
        onSettled();
      } else {
        pushToast(err instanceof Error && err.message ? err.message : t("draft_save_failed_toast"), "error");
      }
    } finally {
      setSaving(false);
    }
  }, [view, current, projectName, episode, adoptView, onSettled, pushToast, t]);

  const discard = useCallback(async (): Promise<boolean> => {
    if (view == null) return false;
    setDiscarding(true);
    try {
      await API.discardEpisodeDraft(projectName, episode, view.doc_type, view.revision);
      pushToast(t("draft_discarded_toast"), "success");
      onSettled();
      return true;
    } catch (err) {
      pushToast(err instanceof Error && err.message ? err.message : t("draft_discard_failed_toast"), "error");
      onSettled();
      return false;
    } finally {
      setDiscarding(false);
    }
  }, [view, projectName, episode, onSettled, pushToast, t]);

  return {
    content: current?.edited ?? null,
    setContent,
    dirty,
    outdated,
    saving,
    discarding,
    save,
    discard,
    reloadLatest,
  };
}
