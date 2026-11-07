import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { API, type AssetMergeEpisodeImpact, type AssetMergeResult, type MergeableAssetType } from "@/api";
import { CopyButton } from "@/components/shared/CopyButton";
import { TruncatedText } from "@/components/shared/TruncatedText";
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
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectData } from "@/types";
import { errMsg } from "@/utils/async";
import { episodeDisplayName, type EpisodeLedger } from "@/utils/episode-display";
import { rejectIfAssetBusy, useAssetBusyNames } from "./assetBusyGuard";

interface MergeAssetDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectName: string;
  assetType: MergeableAssetType;
  name: string;
  description: string;
  /** Disabled state shared by sibling card controls (generating/uploading). */
  busy?: boolean;
  /** Local gallery-card uploads/version restores/deletions are absent from queues; occupied retained assets prevent merging. */
  writingNames?: ReadonlySet<string>;
}

type PreviewState =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "ready"; result: AssetMergeResult }
  | { phase: "failed"; message: string };

/** Reference-rewrite counts in destination order correspond one-to-one with backend report fields. */
const REFERENCE_COUNTS = ["script_plan", "script", "draft", "prompt_text", "speaker"] as const;

function sameTypeNames(project: ProjectData | null, assetType: MergeableAssetType): string[] {
  const bucket =
    assetType === "character" ? project?.characters : assetType === "scene" ? project?.scenes : project?.props;
  return Object.keys(bucket ?? {});
}

/**
 * Merge-into dialog selects same-type retained assets (characters may merge as derivatives), previews per-episode reference rewrites/outdated
 * storyboard/video counts, then merges after confirmation. Display removed descriptions for creator copying.
 *
 * Refetch dry-run previews for every target/method change, invalidating old requests through AbortSignal; enable confirmation
 * only for ready previews matching selections. Merges rewrite all episode references/delete removed assets irreversibly; recheck both parties on opening/submission.
 * Controlled opening from gallery-card more menus.
 */
export function MergeAssetDialog({
  open,
  onOpenChange,
  projectName,
  assetType,
  name,
  description,
  busy = false,
  writingNames,
}: MergeAssetDialogProps) {
  const { t } = useTranslation(["assets", "common"]);
  const project = useProjectsStore((s) => s.currentProjectData);
  const [target, setTarget] = useState("");
  const [asDerivative, setAsDerivative] = useState(false);
  const [preview, setPreview] = useState<PreviewState>({ phase: "idle" });
  const [merging, setMerging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const targetLabelId = useId();
  const modeLabelId = useId();

  useEffect(() => () => controllerRef.current?.abort(), []);

  // Start blank on every opening; already-occupied resources prevent opening.
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setTarget("");
      setAsDerivative(false);
      setPreview({ phase: "idle" });
      setError(null);
    }
  }

  const occupiedNames = useAssetBusyNames(assetType, projectName);
  // Merge is destructive one-click confirmation: newly occupied retained assets disable it immediately with reasons.
  const targetBusyReason = !target
    ? null
    : writingNames?.has(target)
      ? t("assets:merge_target_writing_hint", { name: target })
      : occupiedNames.has(target)
        ? t("assets:merge_target_generating_hint", { name: target })
        : null;

  const candidates = sameTypeNames(project, assetType).filter((candidate) => candidate !== name);
  const episodes: EpisodeLedger = project?.episodes ?? [];

  const rejectIfBusy = (names: string[]) => {
    if (busy || merging) {
      useAppStore.getState().pushToast(t("assets:merge_busy_hint"), "info");
      return true;
    }
    const writing = names.find((asset) => writingNames?.has(asset));
    if (writing !== undefined) {
      useAppStore.getState().pushToast(t("assets:merge_target_writing_hint", { name: writing }), "info");
      return true;
    }
    return names.some((asset) => rejectIfAssetBusy(assetType, projectName, asset, t, "assets:merge_busy_hint"));
  };

  const loadPreview = (nextTarget: string, nextAsDerivative: boolean) => {
    controllerRef.current?.abort();
    if (!nextTarget) {
      setPreview({ phase: "idle" });
      return;
    }
    const controller = new AbortController();
    controllerRef.current = controller;
    setPreview({ phase: "loading" });
    API.mergeProjectAsset(projectName, assetType, name, nextTarget, {
      asDerivative: nextAsDerivative,
      dryRun: true,
      signal: controller.signal,
    })
      .then((result) => {
        if (!controller.signal.aborted) setPreview({ phase: "ready", result });
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setPreview({ phase: "failed", message: errMsg(err) });
      });
  };

  const close = () => {
    controllerRef.current?.abort();
    onOpenChange(false);
  };

  const executeMerge = async () => {
    if (preview.phase !== "ready") return;
    if (rejectIfBusy([name, target])) return;
    setMerging(true);
    setError(null);
    try {
      await API.mergeProjectAsset(projectName, assetType, name, target, { asDerivative });
      onOpenChange(false);
      // Merge committed; refresh is separate. Removed cards disappear on refresh, remaining on-page if refresh fails.
      const refreshed = await useProjectsStore.getState().refreshProject(projectName);
      if (refreshed === "failed") {
        useAppStore.getState().pushToast(t("assets:merge_refresh_failed"), "warning");
      }
    } catch (err) {
      setError(t("assets:merge_failed", { message: errMsg(err) }));
    } finally {
      setMerging(false);
    }
  };

  const episodeLine = (impact: AssetMergeEpisodeImpact) => {
    const separator = t("assets:merge_list_separator");
    const references = REFERENCE_COUNTS.filter((field) => impact[field] > 0)
      .map((field) => t(`assets:merge_count_${field}`, { count: impact[field] }))
      .join(separator);
    const outdated = [
      impact.storyboards > 0 ? t("assets:merge_count_storyboards", { count: impact.storyboards }) : null,
      impact.videos > 0 ? t("assets:merge_count_videos", { count: impact.videos }) : null,
    ]
      .filter((part): part is string => part !== null)
      .join(separator);
    return [references, outdated].filter(Boolean).join(t("assets:merge_clause_separator"));
  };

  let impact: ReactNode = null;
  if (preview.phase === "loading") {
    impact = <p>{t("assets:merge_impact_loading")}</p>;
  } else if (preview.phase === "failed") {
    impact = <p className="text-warn">{t("assets:merge_impact_failed", { message: preview.message })}</p>;
  } else if (preview.phase === "ready") {
    const { result } = preview;
    const storyboards = result.episodes.reduce((sum, item) => sum + item.storyboards, 0);
    const videos = result.episodes.reduce((sum, item) => sum + item.videos, 0);
    impact = (
      <div className="flex flex-col gap-1.5">
        <p className="text-subtle-foreground">
          {result.references > 0
            ? t("assets:merge_impact_summary", { references: result.references, storyboards, videos })
            : t("assets:merge_impact_none", { name })}
        </p>
        {result.episodes.length > 0 && (
          <ul className="flex flex-col gap-1">
            {result.episodes.map((item) => (
              <li key={item.episode} className="flex gap-2">
                <TruncatedText
                  text={episodeDisplayName(episodes, item.episode, t)}
                  className="w-24 shrink-0 text-subtle-foreground"
                />
                <span className="min-w-0 flex-1 tabular-nums">{episodeLine(item)}</span>
              </li>
            ))}
          </ul>
        )}
        {result.aliases_added.length > 0 && (
          <p>{t("assets:merge_aliases_added", { names: result.aliases_added.join(t("assets:merge_list_separator")) })}</p>
        )}
        {result.derivative_created !== null && (
          <p>{t("assets:merge_derivative_created", { name: result.derivative_created })}</p>
        )}
        {result.as_derivative && result.derivative_created === null && (
          <p>{t("assets:merge_derivative_existing", { name: result.source })}</p>
        )}
        {result.derivatives_moved.length > 0 && (
          <p>{t("assets:merge_derivatives_moved", { names: result.derivatives_moved.join(t("assets:merge_list_separator")) })}</p>
        )}
        {result.derivatives_folded.length > 0 && (
          <p>{t("assets:merge_derivatives_folded", { names: result.derivatives_folded.join(t("assets:merge_list_separator")) })}</p>
        )}
      </div>
    );
  }

  const title = t("assets:merge_title", { name });

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        // Ignore Esc during merging so dialogs remain until requests finish.
        if (!next && !merging) close();
      }}
    >
      <AlertDialogContent size="lg">
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{t("assets:merge_discarded", { name })}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogBody tabIndex={0} role="region" aria-label={title}>
          <div className="flex flex-col gap-4 text-sm">
            <div className="flex flex-col gap-1.5">
              <Label id={targetLabelId}>{t("assets:merge_target_label")}</Label>
              {candidates.length === 0 ? (
                <p className="text-subtle-foreground">{t("assets:merge_no_target")}</p>
              ) : (
                <Select
                  value={target || null}
                  onValueChange={(next) => {
                    const value = typeof next === "string" ? next : "";
                    setTarget(value);
                    loadPreview(value, asDerivative);
                  }}
                  disabled={merging}
                >
                  <SelectTrigger aria-labelledby={targetLabelId} className="w-full">
                    <SelectValue placeholder={t("assets:merge_target_placeholder")} />
                  </SelectTrigger>
                  <SelectContent alignItemWithTrigger={false} align="start">
                    {candidates.map((candidate) => (
                      <SelectItem key={candidate} value={candidate}>
                        <TruncatedText text={candidate} focusable={false} />
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>

            {assetType === "character" && (
              <div className="flex flex-col gap-1.5">
                <Label id={modeLabelId}>{t("assets:merge_mode_label")}</Label>
                <RadioGroup
                  aria-labelledby={modeLabelId}
                  value={asDerivative ? "derivative" : "asset"}
                  onValueChange={(next) => {
                    const derivative = next === "derivative";
                    setAsDerivative(derivative);
                    loadPreview(target, derivative);
                  }}
                  disabled={merging}
                >
                  {(["asset", "derivative"] as const).map((mode) => (
                    // eslint-disable-next-line jsx-a11y/label-has-associated-control -- Control is a nested RadioGroupItem (Base UI radio), which the rule cannot detect
                    <label key={mode} className="flex cursor-pointer items-start gap-2.5">
                      <RadioGroupItem value={mode} className="mt-0.5" />
                      <span className="flex min-w-0 flex-col gap-0.5">
                        <span>{t(`assets:merge_mode_${mode}`)}</span>
                        <span className="text-xs text-muted-foreground">{t(`assets:merge_mode_${mode}_hint`, { name })}</span>
                      </span>
                    </label>
                  ))}
                </RadioGroup>
              </div>
            )}

            <div className="flex flex-col gap-1.5">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium">{t("assets:merge_source_description", { name })}</span>
                {description.trim() ? <CopyButton text={description} label={t("assets:merge_copy_description")} /> : null}
              </div>
              <p className="rounded-lg bg-muted/50 px-3 py-2 whitespace-pre-wrap text-subtle-foreground select-text">
                {description.trim() || t("assets:merge_source_description_empty")}
              </p>
            </div>

            {impact}

            {targetBusyReason && <p className="text-warn">{targetBusyReason}</p>}

            {error && (
              <p role="alert" className="text-destructive">
                {error}
              </p>
            )}
          </div>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={merging}>{t("common:cancel")}</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={merging || busy || targetBusyReason !== null || preview.phase !== "ready"}
            onClick={() => void executeMerge()}
          >
            {merging && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
            {merging ? t("assets:merging") : t("assets:merge_confirm")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
