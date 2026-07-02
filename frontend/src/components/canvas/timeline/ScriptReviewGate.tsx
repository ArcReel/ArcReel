import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, CheckCircle2, Clock, Lock, RotateCcw, Save } from "lucide-react";
import { API } from "@/api";
import type {
  DramaNormalizedScript,
  DramaSceneContent,
  NarrationStep1Draft,
  NarrationStep1Segment,
  ScriptReviewState,
  Utterance,
} from "@/types";
import { useAppStore } from "@/stores/app-store";
import { voidPromise } from "@/utils/async";
import { AutoTextarea } from "@/components/ui/AutoTextarea";
import {
  ACCENT_BUTTON_STYLE,
  ACCENT_BTN_CLS,
  CARD_STYLE,
  GHOST_BTN_CLS,
  GHOST_BTN_LG_CLS,
} from "@/components/ui/darkroom-tokens";
import { UtteranceListEditor } from "./UtteranceListEditor";

interface ScriptReviewGateProps {
  projectName: string;
  episode: number;
  contentMode: "narration" | "drama";
}

const SECTION_LABEL_STYLE: React.CSSProperties = {
  color: "var(--color-text-4)",
  letterSpacing: "0.08em",
  fontFamily: "var(--font-mono)",
};

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : "";
}

/** Read-only 资产引用 pills（出场角色 / 场景 / 道具），由 step1 登记、gate 不改。 */
function MetaChips({ items }: { items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="flex flex-wrap gap-1">
      {items.map((name) => (
        <span
          key={name}
          className="rounded border border-hairline bg-bg-grad-a/50 px-1.5 py-0.5 text-[10.5px] text-text-3"
        >
          {name}
        </span>
      ))}
    </div>
  );
}

function SceneHeader({
  id,
  durationSeconds,
  segmentBreak,
}: {
  id: string;
  durationSeconds: number;
  segmentBreak: boolean;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <div className="flex items-center gap-2">
      <span className="rounded bg-bg-grad-a/70 px-1.5 py-0.5 font-mono text-[11px] text-text-2">{id}</span>
      <span className="text-[11px] text-text-4">{durationSeconds}s</span>
      {segmentBreak && (
        <span className="rounded border border-hairline px-1.5 py-0.5 text-[10px] text-text-4">
          {t("review_segment_break")}
        </span>
      )}
    </div>
  );
}

function DramaSceneCard({
  scene,
  disabled,
  onChange,
}: {
  scene: DramaSceneContent;
  disabled: boolean;
  onChange: (patch: Partial<DramaSceneContent>) => void;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <article className="rounded-[10px] border border-hairline p-3.5" style={CARD_STYLE}>
      <div className="mb-3 flex items-start justify-between gap-2">
        <SceneHeader id={scene.scene_id} durationSeconds={scene.duration_seconds} segmentBreak={scene.segment_break} />
        <MetaChips items={scene.characters_in_scene} />
      </div>

      <label className="mb-1 block text-[10.5px]" style={SECTION_LABEL_STYLE}>
        {t("review_utterances_label")}
      </label>
      <UtteranceListEditor
        utterances={scene.utterances}
        disabled={disabled}
        onChange={(utterances: Utterance[]) => onChange({ utterances })}
      />

      <label className="mb-1 mt-3 block text-[10.5px]" style={SECTION_LABEL_STYLE}>
        {t("review_source_text_label")}
      </label>
      <AutoTextarea
        value={scene.source_text}
        disabled={disabled}
        onChange={(source_text) => onChange({ source_text })}
        placeholder={t("review_source_text_placeholder")}
        aria-label={t("review_source_text_label")}
        className="text-text-3"
      />
    </article>
  );
}

function NarrationSegmentCard({
  segment,
  disabled,
  onChange,
}: {
  segment: NarrationStep1Segment;
  disabled: boolean;
  onChange: (patch: Partial<NarrationStep1Segment>) => void;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <article className="rounded-[10px] border border-hairline p-3.5" style={CARD_STYLE}>
      <div className="mb-3 flex items-start justify-between gap-2">
        <SceneHeader
          id={segment.segment_id}
          durationSeconds={segment.duration_seconds}
          segmentBreak={segment.segment_break}
        />
        <MetaChips items={segment.characters_in_segment} />
      </div>

      <label className="mb-1 block text-[10.5px]" style={SECTION_LABEL_STYLE}>
        {t("review_novel_text_label")}
      </label>
      <AutoTextarea
        value={segment.novel_text}
        onChange={(novel_text) => onChange({ novel_text })}
        placeholder={t("review_novel_text_placeholder")}
        aria-label={t("review_novel_text_label")}
        disabled={disabled}
      />
    </article>
  );
}

/** 内容是否有未保存编辑：以序列化比对，draft 由 server content 克隆而来，键序稳定。 */
function isDirty(draft: unknown, serverContent: unknown): boolean {
  if (draft == null) return false;
  return JSON.stringify(draft) !== JSON.stringify(serverContent);
}

/**
 * step1→step2 web 审核 gate 面板：把 step1 结构化中间态在网页结构化呈现、可手动 / agent 编辑，
 * 用户显式确认后才放行 step2 视觉生成。drama（utterances + source_text）与 narration
 * （novel_text）共用本面板。
 */
export function ScriptReviewGate({ projectName, episode, contentMode }: ScriptReviewGateProps) {
  const { t } = useTranslation("dashboard");
  const pushToast = useAppStore((s) => s.pushToast);
  const draftRevision = useAppStore((s) => s.getEntityRevision(`draft:episode_${episode}_step1`));

  const [state, setState] = useState<ScriptReviewState | null>(null);
  const [draft, setDraft] = useState<DramaNormalizedScript | NarrationStep1Draft | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<{ message: string } | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const serverContent = state?.content ?? null;
  const dirty = useMemo(() => isDirty(draft, serverContent), [draft, serverContent]);
  const busy = saving || confirming;

  // 把 dirty 镜像进 ref，供下方拉取 effect 读取最新值，而无需把 dirty 列入 deps（否则每次编辑都会重新拉取）。
  const dirtyRef = useRef(false);
  useEffect(() => {
    dirtyRef.current = dirty;
  }, [dirty]);

  // 采用服务端内容为新草稿（深克隆，避免与服务端态共享引用）。用户主动动作（保存 / 确认）后调用，
  // 总是覆盖本地草稿；setDraft 传值而非更新器，保持纯净、不在更新器内读写 ref。
  const adopt = useCallback((next: ScriptReviewState) => {
    setState(next);
    setDraft(
      next.content ? (JSON.parse(JSON.stringify(next.content)) as DramaNormalizedScript | NarrationStep1Draft) : null,
    );
  }, []);

  const handleRetry = useCallback(() => {
    setLoadError(null);
    setLoading(true);
    setReloadNonce((n) => n + 1);
  }, []);

  useEffect(() => {
    let cancelled = false;
    // 已拿到过任一响应（空态或内容态）后，revision 触发的重新拉取静默刷新、不闪加载态；
    const hadResponse = state != null;
    // 屏上有真实内容可保留时，刷新失败静默保留、不破坏用户视图；无内容（首屏，或空态）时失败才进错误态。
    const hasContent = draft != null;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!hadResponse) setLoading(true);
    API.getScriptReview(projectName, episode)
      .then((next) => {
        if (cancelled) return;
        setLoadError(null);
        setState(next);
        // 外部刷新（挂载 / agent 改 step1 触发的 revision）：用户无未保存编辑时采用服务端内容，
        // 有编辑则仅更新服务端态、保留用户草稿。dirtyRef 读取在 effect 内安全（非 render 期）。
        if (!dirtyRef.current) {
          setDraft(
            next.content
              ? (JSON.parse(JSON.stringify(next.content)) as DramaNormalizedScript | NarrationStep1Draft)
              : null,
          );
        }
      })
      .catch((err) => {
        if (cancelled) return;
        // 屏上无真实内容（首屏失败，或空态后 revision 刷新失败）→ 错误态（区别于空态）+ 重试；
        // 已有内容的静默刷新失败则保留现有内容，不破坏用户视图。
        if (!hasContent) setLoadError({ message: errorMessage(err) });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- state/draft 仅用于决定加载态与错误态分支，加入 deps 会在每次刷新后重新拉取造成循环
  }, [projectName, episode, draftRevision, reloadNonce]);

  const handleSave = useCallback(async () => {
    if (!draft) return;
    setSaving(true);
    try {
      adopt(await API.saveScriptReviewContent(projectName, episode, draft));
      pushToast(t("dashboard:review_saved"), "success");
    } catch (err) {
      pushToast(errorMessage(err) || t("dashboard:save_failed", { message: "" }), "error");
    } finally {
      setSaving(false);
    }
  }, [draft, projectName, episode, adopt, pushToast, t]);

  const handleConfirm = useCallback(async () => {
    setConfirming(true);
    try {
      if (dirty && draft) {
        adopt(await API.saveScriptReviewContent(projectName, episode, draft));
      }
      adopt(await API.confirmScriptReview(projectName, episode));
      pushToast(t("dashboard:review_confirmed"), "success");
    } catch (err) {
      pushToast(errorMessage(err) || t("dashboard:review_confirm_failed"), "error");
    } finally {
      setConfirming(false);
    }
  }, [dirty, draft, projectName, episode, adopt, pushToast, t]);

  const updateDramaScene = (index: number, patch: Partial<DramaSceneContent>) => {
    setDraft((prev) => {
      if (!prev || !("scenes" in prev)) return prev;
      return { ...prev, scenes: prev.scenes.map((s, i) => (i === index ? { ...s, ...patch } : s)) };
    });
  };

  const updateNarrationSegment = (index: number, patch: Partial<NarrationStep1Segment>) => {
    setDraft((prev) => {
      if (!prev || !("segments" in prev)) return prev;
      return { ...prev, segments: prev.segments.map((s, i) => (i === index ? { ...s, ...patch } : s)) };
    });
  };

  if (loading) {
    return <div className="flex h-64 items-center justify-center text-text-4">{t("dashboard:loading_preprocessing")}</div>;
  }

  // 加载错误态：区别于「无 step1 产物」空态，展示错误信息 + 重试入口。
  if (loadError) {
    return (
      <div role="alert" className="flex h-64 flex-col items-center justify-center gap-3 text-center">
        <AlertTriangle className="h-6 w-6 text-amber-400" aria-hidden="true" />
        <div className="flex flex-col gap-1">
          <p className="text-[13px] font-medium text-text-2">{t("dashboard:review_load_failed")}</p>
          {loadError.message && (
            <p className="max-w-sm px-4 font-mono text-[11px] text-text-4">{loadError.message}</p>
          )}
        </div>
        <button type="button" onClick={handleRetry} className={GHOST_BTN_LG_CLS}>
          <RotateCcw className="h-3.5 w-3.5" />
          {t("dashboard:review_retry")}
        </button>
      </div>
    );
  }

  const status = state?.status ?? "no_step1";
  if (status === "no_step1" || draft == null) {
    return (
      <div className="flex h-64 items-center justify-center text-text-4">{t("dashboard:no_preprocessing_content")}</div>
    );
  }

  const confirmed = status === "confirmed" && !dirty;

  return (
    <div className="flex flex-col gap-3">
      {/* 审核状态条 + 确认动作 */}
      <header
        className="sticky top-0 z-10 flex items-center justify-between gap-3 rounded-[10px] border border-hairline px-3.5 py-2.5 backdrop-blur-md"
        style={CARD_STYLE}
      >
        <div className="flex items-center gap-2">
          {confirmed ? (
            <CheckCircle2 className="h-4 w-4 text-emerald-400" />
          ) : (
            <Clock className="h-4 w-4 text-amber-400" />
          )}
          <div className="flex flex-col">
            <span className="text-[12.5px] font-medium text-text">
              {confirmed ? t("dashboard:review_status_confirmed") : t("dashboard:review_status_pending")}
            </span>
            <span className="text-[11px] text-text-4">
              {confirmed ? t("dashboard:review_confirmed_hint") : t("dashboard:review_pending_hint")}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {dirty && (
            <button type="button" onClick={voidPromise(handleSave)} disabled={busy} className={GHOST_BTN_CLS}>
              <Save className="h-3.5 w-3.5" />
              {saving ? t("common:saving") : t("common:save")}
            </button>
          )}
          <button
            type="button"
            onClick={voidPromise(handleConfirm)}
            disabled={busy || confirmed}
            className={ACCENT_BTN_CLS}
            style={ACCENT_BUTTON_STYLE}
          >
            {confirmed ? <Lock className="h-3.5 w-3.5" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
            {confirming
              ? t("dashboard:review_confirming")
              : confirmed
                ? t("dashboard:review_confirmed_badge")
                : t("dashboard:review_confirm_action")}
          </button>
        </div>
      </header>

      {/* 结构化中间态卡片 */}
      <div className="flex flex-col gap-2.5">
        {contentMode === "drama" && "scenes" in draft
          ? draft.scenes.map((scene, i) => (
              <DramaSceneCard
                key={scene.scene_id || i}
                scene={scene}
                disabled={busy}
                onChange={(patch) => updateDramaScene(i, patch)}
              />
            ))
          : null}
        {contentMode === "narration" && "segments" in draft
          ? draft.segments.map((segment, i) => (
              <NarrationSegmentCard
                key={segment.segment_id || i}
                segment={segment}
                disabled={busy}
                onChange={(patch) => updateNarrationSegment(i, patch)}
              />
            ))
          : null}
      </div>
    </div>
  );
}
