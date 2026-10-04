import { useEffect, useId, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Clapperboard, Download, Film, Loader2, RotateCw } from "lucide-react";
import { API } from "@/api";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { errMsg } from "@/utils/async";
import { formatRelativeTime, isJustNow } from "@/utils/date-format";
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

const STATUS_DOT: Record<RenderArtifactStatus, string> = {
  current: "bg-good",
  stale: "bg-warn",
  missing: "bg-muted-foreground",
  blocked: "bg-destructive",
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
 * 提交请求或下载请求在途时忽略关闭请求；任务开始后可以关掉，再打开时接回同一产物的在途任务。
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
  const [kind, setKind] = useState<RenderKind>("final_cut");
  // 草稿目录与剪映版本跨交付物切换保留；下载时才写回 localStorage。
  const [draftPath, setDraftPath] = useState(() => localStorage.getItem(DRAFT_PATH_STORAGE_KEY) ?? "");
  const [jianyingVersion, setJianyingVersion] = useState<JianyingVersion>(() =>
    localStorage.getItem(JIANYING_VERSION_STORAGE_KEY) === "5" ? "5" : "6",
  );
  const [chosenNarration, setNarration] = useState<TimelineNarration>("with_narration");
  const [burnSubtitles, setBurnSubtitles] = useState(true);
  const [busy, setBusy] = useState(false);
  const narration: TimelineNarration = narrationAvailable ? chosenNarration : "without_narration";
  const subtitles: SubtitleMode = burnSubtitles ? "burned_subtitles" : "no_subtitles";
  const { reason: blockedReason } = useBlockedReason(issues, narration);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !busy) onClose();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            <span className="block truncate">{t("edit_render_dialog_title", { name: timelineName })}</span>
          </DialogTitle>
        </DialogHeader>
        {open && (
          <RenderPanel
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
            onBusyChange={setBusy}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

/** 对话框的 Body 与 Footer：按交付物与版本重新挂载，产物现状与任务进度不跨版本残留。 */
function RenderPanel({
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
  onBusyChange,
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
  /** 提交或下载请求在途与否，对话框据此忽略关闭请求。 */
  onBusyChange: (busy: boolean) => void;
}) {
  const { t, i18n } = useTranslation("dashboard");
  const draftPathId = useId();
  const draftHintId = useId();
  const state = useRenderArtifact(
    projectName,
    timelineId,
    kind,
    narration ?? "without_narration",
    burnSubtitles ? "burned_subtitles" : "no_subtitles",
  );
  const { artifact, submitting, requesting } = state;
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const busy = requesting || downloading;
  useEffect(() => {
    onBusyChange(busy);
  }, [busy, onBusyChange]);
  // 换版本重新挂载或对话框关闭时，上一份面板的在途状态不再拦住关闭。
  useEffect(() => () => onBusyChange(false), [onBusyChange]);

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

  const downloadButton = (
    <Button
      variant={downloadIsPrimary ? "default" : "outline"}
      onClick={() => void handleDownload()}
      disabled={!canDownload || submitting}
      title={isDraft && hasFile && !draftReady ? t("edit_render_draft_path_required") : undefined}
    >
      {downloading ? (
        <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden />
      ) : (
        <Download data-icon="inline-start" aria-hidden />
      )}
      {t("edit_render_download")}
    </Button>
  );
  const renderButton = (
    <Button
      variant={hasFile && downloadIsPrimary ? "outline" : "default"}
      onClick={() => void state.submit()}
      disabled={submitting || state.loading || blockedReason !== null}
      title={blockedReason ?? undefined}
    >
      {submitting ? (
        <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden />
      ) : (
        <RotateCw data-icon="inline-start" aria-hidden />
      )}
      {renderLabel}
    </Button>
  );

  return (
    <>
      <DialogBody>
        <div className="flex flex-col gap-4">
          <div role="radiogroup" aria-label={t("edit_render_kind_label")} className="grid grid-cols-2 gap-2">
            <KindOption
              value="final_cut"
              selected={kind === "final_cut"}
              disabled={submitting}
              onSelect={onKindChange}
              icon={<Film aria-hidden className="size-4" />}
              title={t("edit_render_kind_final_cut")}
              hint={t("edit_render_kind_final_cut_hint")}
            />
            <KindOption
              value="jianying_draft"
              selected={kind === "jianying_draft"}
              disabled={submitting}
              onSelect={onKindChange}
              icon={<Clapperboard aria-hidden className="size-4" />}
              title={t("edit_render_kind_jianying_draft")}
              hint={t("edit_render_kind_jianying_draft_hint")}
            />
          </div>

          {narration !== null && (
            <ChoiceRow
              label={t("edit_render_narration_label")}
              value={narration}
              disabled={submitting}
              onChange={(value) => onNarrationChange(value === "with_narration" ? "with_narration" : "without_narration")}
              options={[
                { value: "with_narration", label: t("edit_render_narration_with") },
                { value: "without_narration", label: t("edit_render_narration_without") },
              ]}
            />
          )}
          {!isDraft && (
            <Label className="items-start">
              <Checkbox
                checked={burnSubtitles}
                disabled={submitting}
                onCheckedChange={(checked) => onBurnSubtitlesChange(checked)}
              />
              <span className="flex flex-col gap-0.5">
                {t("edit_render_burn_subtitles")}
                <span className="text-xs text-muted-foreground">{t("edit_render_burn_subtitles_hint")}</span>
              </span>
            </Label>
          )}

          <ArtifactStatusRow
            artifact={artifact}
            loading={state.loading}
            loadError={state.loadError}
            language={i18n.language}
          />

          {isDraft && (
            <>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor={draftPathId}>{t("draft_path")}</Label>
                <Input
                  id={draftPathId}
                  value={draftPath}
                  aria-describedby={draftHintId}
                  onChange={(event) => onDraftPathChange(event.target.value)}
                  placeholder={
                    navigator.userAgent.includes("Windows")
                      ? t("draft_path_default_windows")
                      : t("draft_path_default_mac")
                  }
                />
                <p id={draftHintId} className="text-xs text-muted-foreground">
                  {t("edit_render_draft_fields_hint")}
                </p>
              </div>
              <ChoiceRow
                label={t("jianying_version")}
                value={jianyingVersion}
                onChange={(value) => onJianyingVersionChange(value === "5" ? "5" : "6")}
                options={[
                  { value: "6", label: t("jianying_v6_plus") },
                  { value: "5", label: t("jianying_v5_x") },
                ]}
              />
            </>
          )}

          {blockedReason !== null && <ErrorLine text={blockedReason} />}
          <TaskProgress kind={kind} submitting={submitting} task={state.task} />
          {state.submitError !== null && (
            <ErrorLine text={t("edit_render_submit_failed", { message: state.submitError })} />
          )}
          {downloadError !== null && <ErrorLine text={t("edit_render_download_failed", { message: downloadError })} />}
        </div>
      </DialogBody>
      <DialogFooter>
        {hasFile && (downloadIsPrimary ? <>{renderButton}{downloadButton}</> : <>{downloadButton}{renderButton}</>)}
        {!hasFile && renderButton}
      </DialogFooter>
    </>
  );
}

function KindOption({
  value,
  selected,
  disabled,
  onSelect,
  icon,
  title,
  hint,
}: {
  value: RenderKind;
  selected: boolean;
  disabled: boolean;
  onSelect: (kind: RenderKind) => void;
  icon: ReactNode;
  title: string;
  hint: string;
}) {
  const name = useId();
  return (
    <label
      className={cn(
        "flex cursor-pointer items-start gap-2.5 rounded-lg border px-3 py-2.5 transition-colors has-disabled:cursor-not-allowed has-disabled:opacity-50 has-focus-visible:ring-3 has-focus-visible:ring-ring/50",
        selected ? "border-primary/60 bg-primary/10" : "border-border hover:bg-accent",
      )}
    >
      <input
        type="radio"
        name={name}
        value={value}
        checked={selected}
        disabled={disabled && !selected}
        onChange={() => onSelect(value)}
        className="sr-only"
      />
      <span className={cn("mt-0.5 shrink-0", selected ? "text-primary" : "text-muted-foreground")}>{icon}</span>
      <span className="flex min-w-0 flex-col gap-1">
        <span className="text-sm leading-tight font-medium text-foreground">{title}</span>
        {/* 选中项浅底上用中间档文字，保证对比度 */}
        <span className={cn("text-xs", selected ? "text-subtle-foreground" : "text-muted-foreground")}>{hint}</span>
      </span>
    </label>
  );
}

/** 两三个互斥选项的一行单选：旁白版本、剪映版本。 */
function ChoiceRow({
  label,
  value,
  disabled = false,
  onChange,
  options,
}: {
  label: string;
  value: string;
  disabled?: boolean;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
}) {
  const labelId = useId();
  return (
    <div className="flex flex-col gap-2">
      <span id={labelId} className="text-sm font-medium text-foreground">
        {label}
      </span>
      <RadioGroup
        aria-labelledby={labelId}
        value={value}
        disabled={disabled}
        onValueChange={(next: string) => onChange(next)}
        className="grid-flow-col justify-start"
      >
        {options.map((option) => (
          <Label key={option.value} className="mr-4">
            <RadioGroupItem value={option.value} />
            {option.label}
          </Label>
        ))}
      </RadioGroup>
    </div>
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
    return loading ? <p className="text-xs text-muted-foreground">{t("edit_render_status_loading")}</p> : null;
  }
  const status = artifact.status;
  const when = formatRelativeTime(artifact.rendered_at, language);
  const renderedJustNow = isJustNow(artifact.rendered_at);
  return (
    <div
      data-testid="edit-render-artifact-status"
      data-status={status}
      className="flex items-start gap-2.5 rounded-lg border border-border/50 bg-muted/50 px-3 py-2.5"
    >
      <span aria-hidden className={cn("mt-1.5 size-2 shrink-0 rounded-full", STATUS_DOT[status])} />
      <div className="flex min-w-0 flex-col text-sm">
        <span className="text-foreground">{t(`edit_render_status_${status}`)}</span>
        {artifact.version !== null && (
          <span className="text-xs text-subtle-foreground">
            {renderedJustNow
              ? t("edit_render_status_meta_just_now", { version: artifact.version })
              : when
                ? t("edit_render_status_meta", { version: artifact.version, time: when })
                : t("edit_render_status_version", { version: artifact.version })}
          </span>
        )}
        {status === "stale" && artifact.version !== null && (
          <span className="text-xs text-subtle-foreground">{t("edit_render_status_stale_hint")}</span>
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
    <div role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
      {active && <Loader2 aria-hidden className="size-3.5 animate-spin" />}
      <span>{text}</span>
    </div>
  );
}

function ErrorLine({ text }: { text: string }) {
  return (
    <p role="alert" className="text-sm text-destructive">
      {text}
    </p>
  );
}
