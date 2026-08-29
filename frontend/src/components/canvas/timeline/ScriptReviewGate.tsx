import { useCallback } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, CheckCircle2, Clock, Lock, RotateCcw, Save, Wrench } from "lucide-react";
import type {
  DramaNormalizedScript,
  DramaSceneContent,
  NarrationScriptPlanDraft,
  NarrationScriptPlanSegment,
  ScriptReviewQuarantine,
  ScriptReviewState,
  Utterance,
} from "@/types";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useScriptReviewDraft } from "@/hooks/useScriptReviewDraft";
import { voidPromise } from "@/utils/async";
import { EpisodeDurationSummary } from "@/components/shared/EpisodeDurationSummary";
import { AutoTextarea } from "@/components/ui/AutoTextarea";
import {
  ACCENT_BUTTON_STYLE,
  ACCENT_BTN_CLS,
  CARD_STYLE,
  GHOST_BTN_CLS,
  GHOST_BTN_LG_CLS,
} from "@/components/ui/darkroom-tokens";
import { sumItemDuration } from "@/utils/script-shape";
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

/** 两条 script_plan 变体（drama / narration）的可编辑草稿联合。 */
type ReviewDraft = DramaNormalizedScript | NarrationScriptPlanDraft;

/** 本面板承接 drama / narration 两个变体的内容，reference_video 变体不会路由到这里。 */
function selectReviewContent(state: ScriptReviewState): ReviewDraft | null {
  return (state.content ?? null) as ReviewDraft | null;
}

/** Read-only 资产引用 pills（出场角色 / 场景 / 道具），由 script_plan 登记、gate 不改。 */
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
  segment: NarrationScriptPlanSegment;
  disabled: boolean;
  onChange: (patch: Partial<NarrationScriptPlanSegment>) => void;
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

/**
 * 待修复草稿在场时的只读呈现：违约逐条列出 + 草稿正文原样展示。
 *
 * 不复用上面两个可编辑卡片渲染草稿：草稿正是给 Agent 手改的那一份，字段可能缺失或类型不对，
 * 按可编辑卡片的字段契约渲染要么崩、要么把缺失字段补成用户没写过的值——而渲染崩掉恰好发生在
 * 用户最需要看到面板的时候。原样展示 + 逐条违约足以回答「哪里不对、正在改的是什么」，修复由
 * Agent 在草稿上完成，晋升后本面板自动回到可编辑态。
 */
function QuarantinePanel(props: { quarantine: ScriptReviewQuarantine; onRequestFix: () => void }) {
  const { t } = useTranslation("dashboard");
  const violations = props.quarantine.violations;
  const rawDraft = props.quarantine.content;
  return (
    <div className="flex flex-col gap-2.5">
      <article className="rounded-[10px] border border-hairline p-3.5" style={CARD_STYLE}>
        <div className="mb-2 flex items-center justify-between gap-2">
          <span className="text-[10.5px]" style={SECTION_LABEL_STYLE}>
            {t("review_quarantined_violations_label", { count: violations.length })}
          </span>
          <button type="button" onClick={props.onRequestFix} className={GHOST_BTN_CLS}>
            <Wrench className="h-3.5 w-3.5" />
            {t("review_request_fix")}
          </button>
        </div>
        {violations.length === 0 ? (
          <p className="text-[11.5px] text-text-3">{t("review_quarantined_no_violations")}</p>
        ) : (
          <ol className="flex flex-col gap-1.5">
            {violations.map((violation, i) => (
              <li key={violation.code + "-" + String(i)} className="text-[11.5px] leading-relaxed text-text-2">
                {violation.label && (
                  <span className="mr-1.5 rounded bg-bg-grad-a/70 px-1.5 py-0.5 font-mono text-[10.5px] text-text-3">
                    {violation.label}
                  </span>
                )}
                {violation.message}
              </li>
            ))}
          </ol>
        )}
      </article>

      {rawDraft != null && (
        <article className="rounded-[10px] border border-hairline p-3.5" style={CARD_STYLE}>
          <p className="mb-2 text-[10.5px]" style={SECTION_LABEL_STYLE}>
            {t("review_quarantined_draft_label")}
          </p>
          <pre className="overflow-x-auto whitespace-pre-wrap break-words font-mono text-[11px] text-text-3">
            {JSON.stringify(rawDraft, null, 2)}
          </pre>
        </article>
      )}
    </div>
  );
}

/**
 * script_plan→prompt_authoring web 内容确认面板：把 script_plan 结构化中间态在网页结构化呈现、可手动 / Agent 编辑，
 * 用户显式确认后才放行 prompt_authoring 视觉生成。drama（utterances + source_text）与 narration
 * （novel_text）共用本面板；reference_video 变体的专属面板见 `ReferenceScriptPlanPreviewPanel`。
 *
 * 待修复草稿在场时整面板转只读（见 `QuarantinePanel`）：正式内容此刻仍是上一版，编辑与确认
 * 都无意义——确认端点本就按同一判据拒绝。
 */
export function ScriptReviewGate({ projectName, episode, contentMode }: ScriptReviewGateProps) {
  const { t } = useTranslation("dashboard");
  const pushToast = useAppStore((s) => s.pushToast);

  const handleConfirmed = useCallback(() => {
    pushToast(t("dashboard:review_confirmed"), "success");
  }, [pushToast, t]);

  const {
    state,
    draft,
    setDraft,
    dirty,
    loading,
    loadError,
    saving,
    busy,
    retry: handleRetry,
    save: handleSave,
    confirm: handleConfirm,
    confirming,
  } = useScriptReviewDraft<ReviewDraft>({
    projectName,
    episode,
    selectContent: selectReviewContent,
    onConfirmed: handleConfirmed,
  });

  const updateDramaScene = (index: number, patch: Partial<DramaSceneContent>) => {
    setDraft((prev) => {
      if (!prev || !("scenes" in prev)) return prev;
      return { ...prev, scenes: prev.scenes.map((s, i) => (i === index ? { ...s, ...patch } : s)) };
    });
  };

  const updateNarrationSegment = (index: number, patch: Partial<NarrationScriptPlanSegment>) => {
    setDraft((prev) => {
      if (!prev || !("segments" in prev)) return prev;
      return { ...prev, segments: prev.segments.map((s, i) => (i === index ? { ...s, ...patch } : s)) };
    });
  };

  // 待修复草稿的处置只能由 Agent 在草稿上完成（正式文件写禁、晋升要经工具全量重判），故这里
  // 把逐条违约预填进对话输入框，用户一句话就能把上下文完整交给它，不必自己转述。
  const handleRequestFix = useCallback(() => {
    const violations = state?.quarantine?.violations ?? [];
    const docType = contentMode === "drama" ? "drama_script_plan" : "narration_script_plan";
    // 重算已无违约、但待修复草稿仍在场（Agent 已改对内容、尚未调晋升工具）：不能报「0 处违约
    // 待修复」再让用户去改一份已经没问题的东西，正确的下一步是请 Agent 直接晋升。
    const report =
      violations.length === 0
        ? t("dashboard:review_fix_request_promote_prefill", { episode, docType })
        : [
            t("dashboard:review_fix_request_prefill_header", { episode, count: violations.length, docType }),
            ...violations.map((v, i) => String(i + 1) + ". " + v.message),
          ].join("\n");
    useAssistantStore.getState().setInput(report);
    useAppStore.getState().setAssistantPanelOpen(true);
  }, [state, episode, contentMode, t]);

  if (loading) {
    return <div className="flex h-64 items-center justify-center text-text-4">{t("dashboard:loading_preprocessing")}</div>;
  }

  // 加载错误态：区别于「无 script_plan 产物」空态，展示错误信息 + 重试入口。
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

  const status = state?.status ?? "no_script_plan";
  const quarantine = state?.quarantine ?? null;
  if (status === "no_script_plan" || (draft == null && quarantine == null)) {
    return (
      <div className="flex h-64 items-center justify-center text-text-4">{t("dashboard:no_preprocessing_content")}</div>
    );
  }

  // 待修复草稿在场：正式内容此刻仍是上一版，可编辑态会让用户改一份不会被消费的内容，且确认
  // 端点本就按同一判据拒绝。故整面板转只读，编辑与确认一并锁住。
  const quarantined = quarantine != null;
  const confirmed = status === "confirmed" && !dirty && !quarantined;

  return (
    <div className="flex flex-col gap-3">
      {/* 内容确认状态条 + 确认动作 */}
      <header
        className="sticky top-0 z-10 flex items-center justify-between gap-3 rounded-[10px] border border-hairline px-3.5 py-2.5 backdrop-blur-md"
        style={CARD_STYLE}
      >
        <div className="flex items-center gap-2">
          {quarantined ? (
            <AlertTriangle className="h-4 w-4 text-amber-400" />
          ) : confirmed ? (
            <CheckCircle2 className="h-4 w-4 text-emerald-400" />
          ) : (
            <Clock className="h-4 w-4 text-amber-400" />
          )}
          <div className="flex flex-col">
            <span className="text-[12.5px] font-medium text-text">
              {quarantined
                ? t("dashboard:review_status_quarantined")
                : confirmed
                  ? t("dashboard:review_status_confirmed")
                  : t("dashboard:review_status_pending")}
            </span>
            <span className="text-[11px] text-text-4">
              {quarantined
                ? t("dashboard:review_quarantined_hint")
                : confirmed
                  ? t("dashboard:review_confirmed_hint")
                  : t("dashboard:review_pending_hint")}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {dirty && !quarantined && (
            <button type="button" onClick={voidPromise(handleSave)} disabled={busy} className={GHOST_BTN_CLS}>
              <Save className="h-3.5 w-3.5" />
              {saving ? t("common:saving") : t("dashboard:review_save_action")}
            </button>
          )}
          <button
            type="button"
            onClick={voidPromise(handleConfirm)}
            disabled={busy || confirmed || quarantined}
            title={quarantined ? t("dashboard:review_confirm_blocked_quarantined") : undefined}
            className={ACCENT_BTN_CLS}
            style={ACCENT_BUTTON_STYLE}
          >
            {quarantined || confirmed ? <Lock className="h-3.5 w-3.5" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
            {confirming
              ? t("dashboard:review_confirming")
              : confirmed
                ? t("dashboard:review_confirmed_badge")
                : t("dashboard:review_confirm_action")}
          </button>
        </div>
      </header>

      {/* 本集合计与项目目标的对比；未设目标时不渲染，超出只提示不阻断确认 */}
      {!quarantined && (
        <EpisodeDurationSummary
          totalSeconds={sumItemDuration(
            draft == null ? [] : "scenes" in draft ? draft.scenes : draft.segments,
          )}
          targetSeconds={state?.episode_target_duration ?? null}
        />
      )}

      {/* 结构化中间态卡片；待修复草稿在场时改为只读的违约面板 */}
      {quarantined ? (
        <QuarantinePanel quarantine={quarantine} onRequestFix={handleRequestFix} />
      ) : (
        <div className="flex flex-col gap-2.5">
          {contentMode === "drama" && draft != null && "scenes" in draft
            ? draft.scenes.map((scene, i) => (
                <DramaSceneCard
                  key={scene.scene_id || i}
                  scene={scene}
                  disabled={busy}
                  onChange={(patch) => updateDramaScene(i, patch)}
                />
              ))
            : null}
          {contentMode === "narration" && draft != null && "segments" in draft
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
      )}
    </div>
  );
}
