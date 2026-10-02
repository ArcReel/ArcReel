import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Merge, MoreHorizontal } from "lucide-react";
import { API, type AssetMergeEpisodeImpact, type AssetMergeResult, type MergeableAssetType } from "@/api";
import { ActionMenu } from "@/components/ui/ActionMenu";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { CopyButton } from "@/components/ui/CopyButton";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectData } from "@/types";
import { errMsg } from "@/utils/async";
import { episodeDisplayName, type EpisodeLedger } from "@/utils/episode-display";
import { rejectIfAssetBusy } from "./assetBusyGuard";

interface MergeAssetMenuProps {
  projectName: string;
  assetType: MergeableAssetType;
  name: string;
  description: string;
  /** 与卡片兄弟控件共享的禁用态（生成中 / 上传中）。 */
  busy?: boolean;
}

type PreviewState =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "ready"; result: AssetMergeResult }
  | { phase: "failed"; message: string };

const TRIGGER_CLS =
  "focus-ring inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--color-text-3)] transition-colors hover:bg-[oklch(1_0_0_/_0.05)] disabled:cursor-not-allowed disabled:opacity-40";

const FIELD_STYLE = {
  background: "oklch(0.20 0.011 265 / 0.6)",
  border: "1px solid var(--color-hairline)",
  color: "var(--color-text)",
} as const;

const LABEL_CLS = "block text-[11px] font-medium";

/** 引用改写数按落点列出的顺序，与后端报告字段一一对应。 */
const REFERENCE_COUNTS = ["script_plan", "script", "draft", "prompt_text", "speaker"] as const;

function sameTypeNames(project: ProjectData | null, assetType: MergeableAssetType): string[] {
  const bucket =
    assetType === "character" ? project?.characters : assetType === "scene" ? project?.scenes : project?.props;
  return Object.keys(bucket ?? {});
}

/**
 * 资产卡片的操作菜单与「并入…」对话框：选同类型的保留方（角色还可选并为衍生），按集预览将改写的
 * 引用数与将过期的分镜图、视频数，确认后执行合并。被并方的描述在对话框里展示，供创作者复制取用。
 *
 * 预览随保留方与并法的每次改动重取（dry-run），旧请求经 AbortSignal 作废；只有与当前选择对应的
 * 预览就绪后才能确认。合并改写全部剧集引用并删除被并方，打开与提交时都复核双方的占用态。
 */
export function MergeAssetMenu({ projectName, assetType, name, description, busy = false }: MergeAssetMenuProps) {
  const { t } = useTranslation(["assets", "common"]);
  const project = useProjectsStore((s) => s.currentProjectData);
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState("");
  const [asDerivative, setAsDerivative] = useState(false);
  const [preview, setPreview] = useState<PreviewState>({ phase: "idle" });
  const [merging, setMerging] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);
  const targetFieldId = useId();
  const modeName = useId();

  useEffect(() => () => controllerRef.current?.abort(), []);

  const candidates = sameTypeNames(project, assetType).filter((candidate) => candidate !== name);
  const episodes: EpisodeLedger = project?.episodes ?? [];

  const rejectIfBusy = (names: string[]) => {
    if (busy || merging) {
      useAppStore.getState().pushToast(t("assets:merge_busy_hint"), "info");
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

  const openDialog = () => {
    if (rejectIfBusy([name])) return;
    setTarget("");
    setAsDerivative(false);
    setPreview({ phase: "idle" });
    setOpen(true);
  };

  const closeDialog = () => {
    controllerRef.current?.abort();
    setOpen(false);
  };

  const executeMerge = async () => {
    if (preview.phase !== "ready") return;
    if (rejectIfBusy([name, target])) return;
    setMerging(true);
    try {
      const result = await API.mergeProjectAsset(projectName, assetType, name, target, { asDerivative });
      useAppStore
        .getState()
        .pushToast(t("assets:merge_success", { source: result.source, target: result.target }), "success");
      setOpen(false);
      // 合并已提交，刷新是独立的后续步骤：被并方的卡片随刷新消失，刷新失败时它会停在页面上。
      const refreshed = await useProjectsStore.getState().refreshProject(projectName);
      if (refreshed === "failed") {
        useAppStore.getState().pushToast(t("assets:merge_refresh_failed"), "warning");
      }
    } catch (err) {
      useAppStore.getState().pushToast(t("assets:merge_failed", { message: errMsg(err) }), "error");
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
    impact = <p style={{ color: "var(--color-warm)" }}>{t("assets:merge_impact_failed", { message: preview.message })}</p>;
  } else if (preview.phase === "ready") {
    const { result } = preview;
    const storyboards = result.episodes.reduce((sum, item) => sum + item.storyboards, 0);
    const videos = result.episodes.reduce((sum, item) => sum + item.videos, 0);
    impact = (
      <div className="space-y-1.5">
        <p style={{ color: "var(--color-text-2)" }}>
          {result.references > 0
            ? t("assets:merge_impact_summary", { references: result.references, storyboards, videos })
            : t("assets:merge_impact_none", { name })}
        </p>
        {result.episodes.length > 0 && (
          <ul className="max-h-40 space-y-1 overflow-y-auto pr-1">
            {result.episodes.map((item) => (
              <li key={item.episode} className="flex gap-2">
                <span className="w-24 shrink-0 truncate" style={{ color: "var(--color-text-2)" }}>
                  {episodeDisplayName(episodes, item.episode, t)}
                </span>
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

  const form = (
    <div className="mt-2 space-y-3.5">
      <div>
        <label htmlFor={targetFieldId} className={LABEL_CLS} style={{ color: "var(--color-text-2)" }}>
          {t("assets:merge_target_label")}
        </label>
        {candidates.length === 0 ? (
          <p className="mt-1">{t("assets:merge_no_target")}</p>
        ) : (
          <select
            id={targetFieldId}
            value={target}
            onChange={(e) => {
              setTarget(e.target.value);
              loadPreview(e.target.value, asDerivative);
            }}
            disabled={merging}
            className="focus-ring mt-1 w-full rounded-lg px-3 py-2 text-[13px] outline-none disabled:cursor-not-allowed disabled:opacity-60"
            style={FIELD_STYLE}
          >
            <option value="" disabled>
              {t("assets:merge_target_placeholder")}
            </option>
            {candidates.map((candidate) => (
              <option key={candidate} value={candidate}>
                {candidate}
              </option>
            ))}
          </select>
        )}
      </div>

      {assetType === "character" && (
        <fieldset disabled={merging} className="space-y-1.5">
          <legend className={LABEL_CLS} style={{ color: "var(--color-text-2)" }}>
            {t("assets:merge_mode_label")}
          </legend>
          {[false, true].map((derivative) => {
            const optionId = `${modeName}-${derivative ? "derivative" : "asset"}`;
            return (
              <div key={optionId} className="flex items-start gap-2">
                <input
                  id={optionId}
                  type="radio"
                  name={modeName}
                  checked={asDerivative === derivative}
                  onChange={() => {
                    setAsDerivative(derivative);
                    loadPreview(target, derivative);
                  }}
                  className="mt-0.5 accent-[var(--color-accent-2)]"
                />
                <label htmlFor={optionId} className="cursor-pointer">
                  <span style={{ color: "var(--color-text)" }}>
                    {t(derivative ? "assets:merge_mode_derivative" : "assets:merge_mode_asset")}
                  </span>
                  <span className="block">
                    {t(derivative ? "assets:merge_mode_derivative_hint" : "assets:merge_mode_asset_hint", { name })}
                  </span>
                </label>
              </div>
            );
          })}
        </fieldset>
      )}

      <div>
        <div className="flex items-center justify-between gap-2">
          <span className={LABEL_CLS} style={{ color: "var(--color-text-2)" }}>
            {t("assets:merge_source_description", { name })}
          </span>
          {description.trim() ? <CopyButton text={description} label={t("assets:merge_copy_description")} /> : null}
        </div>
        <p
          className="mt-1 max-h-24 select-text overflow-y-auto whitespace-pre-wrap rounded-lg px-3 py-2"
          style={FIELD_STYLE}
        >
          {description.trim() || t("assets:merge_source_description_empty")}
        </p>
      </div>

      {impact}

      <p style={{ color: "var(--color-warm)" }}>{t("assets:merge_discarded", { name })}</p>
    </div>
  );

  return (
    <>
      <ActionMenu
        label={t("assets:asset_menu_label", { name })}
        triggerClassName={TRIGGER_CLS}
        items={[
          {
            key: "merge",
            label: t("assets:merge_into"),
            icon: Merge,
            disabled: busy || merging,
            title: busy ? t("assets:merge_busy_hint") : undefined,
            onSelect: openDialog,
          },
        ]}
      >
        <MoreHorizontal className="h-3.5 w-3.5" aria-hidden />
      </ActionMenu>
      <ConfirmDialog
        open={open}
        tone="danger"
        title={t("assets:merge_title", { name })}
        description={form}
        confirmLabel={t("assets:merge_confirm")}
        loadingLabel={t("assets:merging")}
        loading={merging}
        confirmDisabled={preview.phase !== "ready"}
        onConfirm={executeMerge}
        onCancel={closeDialog}
      />
    </>
  );
}
