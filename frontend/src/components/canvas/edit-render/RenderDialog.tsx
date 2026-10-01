import { useId, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Clapperboard, Download, Film, Loader2, RotateCw } from "lucide-react";
import { API } from "@/api";
import { GlassModal } from "@/components/ui/GlassModal";
import { ModalCloseButton } from "@/components/ui/ModalCloseButton";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { SecondaryButton } from "@/components/ui/SecondaryButton";
import { errMsg } from "@/utils/async";
import { formatRelativeTime } from "@/utils/date-format";
import { triggerBrowserDownload } from "@/utils/download";
import type {
  EditTimelineIssueRef,
  JianyingVersion,
  RenderArtifactStatus,
  RenderKind,
  SubtitleMode,
  TaskItem,
} from "@/types";
import type { TimelineNarration } from "@/types/edit-timeline";
import { useBlockedReason } from "./useBlockedReason";
import { useRenderArtifact, type RenderArtifactView } from "./useRenderArtifact";

const DRAFT_PATH_STORAGE_KEY = "arcreel_jianying_draft_path";
const JIANYING_VERSION_STORAGE_KEY = "arcreel_jianying_version";

const FIELD_STYLE = {
  background: "oklch(0.16 0.010 265 / 0.6)",
  border: "1px solid var(--color-hairline)",
  color: "var(--color-text)",
} as const;

const STATUS_COLOR: Record<RenderArtifactStatus, string> = {
  current: "var(--color-good)",
  stale: "var(--color-warm)",
  missing: "var(--color-text-4)",
  blocked: "var(--color-danger)",
};

interface RenderDialogProps {
  open: boolean;
  onClose: () => void;
  projectName: string;
  timelineId: string;
  timelineName: string;
  /** 当前修订的 issues；按所选旁白版本筛出阻断，对话框打开期间出现阻断时，出片按钮随之不可点。 */
  issues: readonly EditTimelineIssueRef[];
  /** 项目用 TTS 配音时可以选旁白版本；否则只出不带旁白版本。 */
  narrationAvailable: boolean;
}

/**
 * 「出片 · <剪辑时间线名称>」对话框：以剪辑视图当前标签的剪辑时间线为准，
 * 选成片或剪映草稿及其版本，查看已有产物的时效，直接下载或重新渲染，提交后显示任务进度。
 *
 * 旁白版本默认带旁白（TTS 配音项目），成片默认烧入字幕；各版本是独立的产物。
 */
export function RenderDialog({
  open,
  onClose,
  projectName,
  timelineId,
  timelineName,
  issues,
  narrationAvailable,
}: RenderDialogProps) {
  const { t } = useTranslation("dashboard");
  const titleId = useId();
  const [kind, setKind] = useState<RenderKind>("final_cut");
  // 草稿目录与剪映版本跨交付物切换保留；下载时才写回 localStorage。
  const [draftPath, setDraftPath] = useState(() => localStorage.getItem(DRAFT_PATH_STORAGE_KEY) ?? "");
  const [jianyingVersion, setJianyingVersion] = useState<JianyingVersion>(() =>
    localStorage.getItem(JIANYING_VERSION_STORAGE_KEY) === "5" ? "5" : "6",
  );
  const [chosenNarration, setNarration] = useState<TimelineNarration>("with_narration");
  const [burnSubtitles, setBurnSubtitles] = useState(true);
  const narration: TimelineNarration = narrationAvailable ? chosenNarration : "without_narration";
  const subtitles: SubtitleMode = burnSubtitles ? "burned_subtitles" : "no_subtitles";
  const { reason: blockedReason } = useBlockedReason(issues, narration);

  return (
    <GlassModal open={open} onClose={onClose} labelledBy={titleId} widthClassName="w-full max-w-lg">
      <div className="flex items-start justify-between gap-3 px-5 pb-3 pt-4">
        <h2
          id={titleId}
          className="display-serif min-w-0 truncate text-[15px] font-semibold tracking-tight"
          style={{ color: "var(--color-text)" }}
        >
          {t("edit_render_dialog_title", { name: timelineName })}
        </h2>
        <ModalCloseButton onClick={onClose} />
      </div>
      {open && (
        <div className="px-5 pb-5">
          <RenderPanelBody
            key={`${projectName}::${timelineId}::${kind}::${narration}::${subtitles}`}
            projectName={projectName}
            timelineId={timelineId}
            timelineName={timelineName}
            kind={kind}
            onKindChange={setKind}
            narration={narrationAvailable ? narration : null}
            onNarrationChange={setNarration}
            burnSubtitles={burnSubtitles}
            onBurnSubtitlesChange={setBurnSubtitles}
            draftPath={draftPath}
            onDraftPathChange={setDraftPath}
            jianyingVersion={jianyingVersion}
            onJianyingVersionChange={setJianyingVersion}
            blockedReason={blockedReason}
          />
        </div>
      )}
    </GlassModal>
  );
}

function RenderPanelBody({
  projectName,
  timelineId,
  timelineName,
  kind,
  onKindChange,
  narration,
  onNarrationChange,
  burnSubtitles,
  onBurnSubtitlesChange,
  draftPath,
  onDraftPathChange,
  jianyingVersion,
  onJianyingVersionChange,
  blockedReason,
}: {
  projectName: string;
  timelineId: string;
  timelineName: string;
  kind: RenderKind;
  onKindChange: (kind: RenderKind) => void;
  /** 所选旁白版本；项目不能选旁白版本时为 null，按不带旁白版本出片。 */
  narration: TimelineNarration | null;
  onNarrationChange: (narration: TimelineNarration) => void;
  burnSubtitles: boolean;
  onBurnSubtitlesChange: (burn: boolean) => void;
  draftPath: string;
  onDraftPathChange: (path: string) => void;
  jianyingVersion: JianyingVersion;
  onJianyingVersionChange: (version: JianyingVersion) => void;
  blockedReason: string | null;
}) {
  const { t, i18n } = useTranslation("dashboard");
  const state = useRenderArtifact(
    projectName,
    timelineId,
    kind,
    narration ?? "without_narration",
    burnSubtitles ? "burned_subtitles" : "no_subtitles",
  );
  const { artifact, submitting } = state;
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const isDraft = kind === "jianying_draft";
  const hasFile = artifact !== null && artifact.version !== null;
  const draftReady = draftPath.trim().length > 0;
  const canDownload = hasFile && !downloading && (!isDraft || draftReady);

  const handleDownload = async () => {
    if (!artifact || !canDownload) return;
    setDownloadError(null);
    if (!isDraft) {
      const url = "download_url" in artifact ? artifact.download_url : null;
      if (url) triggerBrowserDownload(url, `${timelineName}.mp4`);
      return;
    }
    const root = draftPath.trim();
    localStorage.setItem(DRAFT_PATH_STORAGE_KEY, root);
    localStorage.setItem(JIANYING_VERSION_STORAGE_KEY, jianyingVersion);
    setDownloading(true);
    try {
      const { download_token } = await API.requestExportToken(projectName, "current");
      triggerBrowserDownload(
        API.getJianyingDraftDownloadUrl(
          projectName,
          timelineId,
          root,
          download_token,
          jianyingVersion,
          narration ?? "without_narration",
        ),
      );
    } catch (err) {
      setDownloadError(errMsg(err));
    } finally {
      setDownloading(false);
    }
  };

  const renderLabel = hasFile
    ? t(isDraft ? "edit_render_reexport_draft" : "edit_render_rerender_final_cut")
    : t(isDraft ? "edit_render_export_draft" : "edit_render_render_final_cut");
  // 已是最新时下载是主动作；过时或没有产物时（重新）出片是主动作。
  const downloadIsPrimary = artifact?.status === "current";

  const downloadButtonProps = {
    size: "sm" as const,
    onClick: () => void handleDownload(),
    disabled: !canDownload || submitting,
    title: isDraft && hasFile && !draftReady ? t("edit_render_draft_path_required") : undefined,
    leadingIcon: downloading ? (
      <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
    ) : (
      <Download className="h-3.5 w-3.5" aria-hidden="true" />
    ),
    children: t("edit_render_download"),
  };
  const renderButtonProps = {
    size: "sm" as const,
    onClick: () => void state.submit(),
    disabled: submitting || state.loading || blockedReason !== null,
    title: blockedReason ?? undefined,
    leadingIcon: submitting ? (
      <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
    ) : (
      <RotateCw className="h-3.5 w-3.5" aria-hidden="true" />
    ),
    children: renderLabel,
  };

  return (
    <div className="flex flex-col gap-3">
      <div role="radiogroup" aria-label={t("edit_render_kind_label")} className="grid grid-cols-2 gap-2">
        <KindOption
          selected={kind === "final_cut"}
          disabled={submitting}
          onSelect={() => onKindChange("final_cut")}
          icon={<Film className="h-4 w-4" aria-hidden="true" />}
          title={t("edit_render_kind_final_cut")}
          hint={t("edit_render_kind_final_cut_hint")}
        />
        <KindOption
          selected={kind === "jianying_draft"}
          disabled={submitting}
          onSelect={() => onKindChange("jianying_draft")}
          icon={<Clapperboard className="h-4 w-4" aria-hidden="true" />}
          title={t("edit_render_kind_jianying_draft")}
          hint={t("edit_render_kind_jianying_draft_hint")}
        />
      </div>

      {(narration !== null || !isDraft) && (
        <div className="flex flex-col gap-3">
          {narration !== null && (
            <Field htmlFor="edit-render-narration" label={t("edit_render_narration_label")}>
              <select
                id="edit-render-narration"
                value={narration}
                disabled={submitting}
                onChange={(event) =>
                  onNarrationChange(event.target.value === "with_narration" ? "with_narration" : "without_narration")
                }
                className="focus-ring w-full rounded-md px-2.5 py-1.5 text-[13px] outline-none disabled:cursor-not-allowed disabled:opacity-50"
                style={FIELD_STYLE}
              >
                <option value="with_narration">{t("edit_render_narration_with")}</option>
                <option value="without_narration">{t("edit_render_narration_without")}</option>
              </select>
            </Field>
          )}
          {!isDraft && (
            <label className="flex cursor-pointer items-start gap-2 text-[12.5px]" style={{ color: "var(--color-text-2)" }}>
              <input
                type="checkbox"
                className="mt-0.5"
                checked={burnSubtitles}
                disabled={submitting}
                onChange={(event) => onBurnSubtitlesChange(event.target.checked)}
              />
              <span>
                {t("edit_render_burn_subtitles")}
                <span className="mt-0.5 block text-[11.5px]" style={{ color: "var(--color-text-4)" }}>
                  {t("edit_render_burn_subtitles_hint")}
                </span>
              </span>
            </label>
          )}
        </div>
      )}

      <ArtifactStatusRow
        artifact={artifact}
        loading={state.loading}
        loadError={state.loadError}
        language={i18n.language}
      />

      {isDraft && (
        <div className="flex flex-col gap-3">
          <Field htmlFor="edit-render-draft-path" label={t("draft_path")} hint={t("edit_render_draft_fields_hint")}>
            <input
              id="edit-render-draft-path"
              type="text"
              value={draftPath}
              onChange={(event) => onDraftPathChange(event.target.value)}
              placeholder={
                navigator.userAgent.includes("Windows")
                  ? t("draft_path_default_windows")
                  : t("draft_path_default_mac")
              }
              className="focus-ring w-full rounded-md px-2.5 py-1.5 text-[12.5px] outline-none"
              style={{ ...FIELD_STYLE, fontFamily: "var(--font-mono)" }}
            />
          </Field>
          <Field htmlFor="edit-render-jianying-version" label={t("jianying_version")}>
            <select
              id="edit-render-jianying-version"
              value={jianyingVersion}
              onChange={(event) => onJianyingVersionChange(event.target.value === "5" ? "5" : "6")}
              className="focus-ring w-full rounded-md px-2.5 py-1.5 text-[13px] outline-none"
              style={FIELD_STYLE}
            >
              <option value="6">{t("jianying_v6_plus")}</option>
              <option value="5">{t("jianying_v5_x")}</option>
            </select>
          </Field>
        </div>
      )}

      {blockedReason !== null && <ErrorLine text={blockedReason} />}
      <TaskProgress kind={kind} submitting={submitting} task={state.task} />
      {state.submitError !== null && <ErrorLine text={t("edit_render_submit_failed", { message: state.submitError })} />}
      {downloadError !== null && <ErrorLine text={t("edit_render_download_failed", { message: downloadError })} />}

      <div className="flex flex-wrap items-center justify-end gap-2 pt-1">
        {hasFile &&
          (downloadIsPrimary ? (
            <>
              <SecondaryButton {...renderButtonProps} />
              <PrimaryButton tone="accent" {...downloadButtonProps} />
            </>
          ) : (
            <>
              <SecondaryButton {...downloadButtonProps} />
              <PrimaryButton tone="accent" {...renderButtonProps} />
            </>
          ))}
        {!hasFile && <PrimaryButton tone="accent" {...renderButtonProps} />}
      </div>
    </div>
  );
}

function KindOption({
  selected,
  disabled,
  onSelect,
  icon,
  title,
  hint,
}: {
  selected: boolean;
  disabled: boolean;
  onSelect: () => void;
  icon: ReactNode;
  title: string;
  hint: string;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      disabled={disabled && !selected}
      onClick={onSelect}
      className="focus-ring flex items-start gap-2.5 rounded-lg px-3 py-2.5 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50"
      style={{
        border: `1px solid ${selected ? "var(--color-accent-soft)" : "var(--color-hairline)"}`,
        background: selected ? "var(--color-accent-dim)" : "oklch(0.20 0.011 265 / 0.4)",
      }}
    >
      <span className="mt-0.5 shrink-0" style={{ color: selected ? "var(--color-accent-2)" : "var(--color-text-3)" }}>
        {icon}
      </span>
      <span className="min-w-0">
        <span className="block text-[13px] font-medium leading-tight" style={{ color: "var(--color-text)" }}>
          {title}
        </span>
        <span className="mt-1 block text-[11.5px] leading-[1.5]" style={{ color: "var(--color-text-4)" }}>
          {hint}
        </span>
      </span>
    </button>
  );
}

function ArtifactStatusRow({
  artifact,
  loading,
  loadError,
  language,
}: {
  artifact: RenderArtifactView | null;
  loading: boolean;
  loadError: string | null;
  language: string;
}) {
  const { t } = useTranslation("dashboard");
  if (loadError !== null) return <ErrorLine text={t("edit_render_status_load_failed", { message: loadError })} />;
  if (artifact === null) {
    return (
      <p className="text-[12px]" style={{ color: "var(--color-text-4)" }}>
        {loading ? t("edit_render_status_loading") : null}
      </p>
    );
  }
  const status = artifact.status;
  const when = formatRelativeTime(artifact.rendered_at, language);
  return (
    <div
      data-testid="edit-render-artifact-status"
      data-status={status}
      className="flex items-start gap-2.5 rounded-lg px-3 py-2.5"
      style={{ border: "1px solid var(--color-hairline-soft)", background: "oklch(0.18 0.010 265 / 0.5)" }}
    >
      <span
        aria-hidden="true"
        className="mt-[5px] h-2 w-2 shrink-0 rounded-full"
        style={{ background: STATUS_COLOR[status] }}
      />
      <div className="min-w-0 text-[12.5px] leading-[1.5]">
        <div style={{ color: "var(--color-text)" }}>{t(`edit_render_status_${status}`)}</div>
        {artifact.version !== null && (
          <div style={{ color: "var(--color-text-4)" }}>
            {when
              ? t("edit_render_status_meta", { version: artifact.version, time: when })
              : t("edit_render_status_version", { version: artifact.version })}
          </div>
        )}
        {status === "stale" && artifact.version !== null && (
          <div style={{ color: "var(--color-text-4)" }}>{t("edit_render_status_stale_hint")}</div>
        )}
      </div>
    </div>
  );
}

function TaskProgress({ kind, submitting, task }: { kind: RenderKind; submitting: boolean; task: TaskItem | null }) {
  const { t } = useTranslation("dashboard");
  if (!submitting && task === null) return null;
  if (task?.status === "failed") {
    return (
      <ErrorLine
        text={t("edit_render_task_failed", { message: task.error_message ?? t("edit_render_task_failed_unknown") })}
      />
    );
  }
  let text: string;
  if (task === null || task.status === "queued") text = t("edit_render_task_queued");
  else if (task.status === "running")
    text = t(kind === "final_cut" ? "edit_render_task_running_final_cut" : "edit_render_task_running_draft");
  else if (task.status === "succeeded") text = t("edit_render_task_succeeded");
  else text = t("edit_render_task_cancelled");
  const active = submitting && task?.status !== "succeeded";
  return (
    <div role="status" className="flex items-center gap-2 text-[12.5px]" style={{ color: "var(--color-text-3)" }}>
      {active && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
      <span>{text}</span>
    </div>
  );
}

function ErrorLine({ text }: { text: string }) {
  return (
    <p role="alert" className="text-[12px] leading-[1.5]" style={{ color: "var(--color-danger)" }}>
      {text}
    </p>
  );
}

function Field({
  htmlFor,
  label,
  hint,
  children,
}: {
  htmlFor: string;
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div>
      <label htmlFor={htmlFor} className="mb-1 block text-[11.5px]" style={{ color: "var(--color-text-3)" }}>
        {label}
      </label>
      {children}
      {hint && (
        <p className="mt-1.5 text-[11px] leading-[1.55]" style={{ color: "var(--color-text-4)" }}>
          {hint}
        </p>
      )}
    </div>
  );
}
