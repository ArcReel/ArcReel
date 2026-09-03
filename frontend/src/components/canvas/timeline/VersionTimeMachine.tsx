import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight, Download, History } from "lucide-react";
import { API, type VersionInfo } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { errMsg } from "@/utils/async";
import { PresentationPlayer } from "@/components/shared/PresentationPlayer";

interface VersionTimeMachineProps {
  projectName: string;
  resourceType: "storyboards" | "videos" | "audio" | "characters" | "character_derivatives" | "scenes" | "props" | "products" | "reference_videos" | "grids";
  resourceId: string;
  onRestore?: (version: number) => void | Promise<void>;
  /** Icon-only trigger button: hides label and chevron for narrow card headers. */
  iconOnly?: boolean;
  /** Allow preview/download history without exposing the restore mutation. */
  readOnly?: boolean;
  /**
   * 同资源正被生成/编辑占用（含 image_edit 乐观占用）：禁用版本恢复。
   * image_edit 任务完成时会无条件把 current 覆盖为编辑结果，占用期间恢复旧版本会
   * 显示成功但随后被编辑任务覆盖，用户最后一次选择丢失。
   */
  busy?: boolean;
  /**
   * 恢复请求在途状态回传父级：`busy` 只做「外部占用 → 禁恢复」这一向，兄弟控件
   * （生成、上传）还需反向知道恢复正在写同一个资源文件，否则恢复返回前它们仍可点，
   * 两个请求并发写同一路径、后完成者覆盖前者且双方都提示成功。
   */
  onRestoringChange?: (restoring: boolean) => void;
  /**
   * 提交时刻的占用复核（新鲜读）：`busy` 是最近一次渲染的快照，版本面板打开期间
   * Agent 入队、批量入口或轮询落库都可能占用该资源，新 prop 冲刷到按钮之前的点击
   * 仍会发出恢复请求，与在跑的任务并发写同一个资源文件。返回 true 即拒绝本次恢复。
   */
  checkBusy?: () => boolean;
}

function getImagePreviewHeightClass(
  resourceType: VersionTimeMachineProps["resourceType"],
): string {
  if (resourceType === "characters" || resourceType === "character_derivatives") return "h-80";
  if (resourceType === "scenes" || resourceType === "props" || resourceType === "products") return "h-56";
  return "h-64";
}

/** Find all scrollable ancestor elements. */
function getScrollParents(el: HTMLElement): HTMLElement[] {
  const parents: HTMLElement[] = [];
  let node: HTMLElement | null = el.parentElement;
  while (node) {
    const s = getComputedStyle(node);
    if (/(auto|scroll)/.test(s.overflow + s.overflowY)) parents.push(node);
    node = node.parentElement;
  }
  return parents;
}

export function VersionTimeMachine({
  projectName,
  resourceType,
  resourceId,
  onRestore,
  iconOnly = false,
  readOnly = false,
  busy = false,
  onRestoringChange,
  checkBusy,
}: VersionTimeMachineProps) {
  const { t } = useTranslation("dashboard");
  const resourcePath =
    resourceType === "storyboards" ? `storyboards/scene_${resourceId}.png` :
    resourceType === "videos" ? `videos/scene_${resourceId}.mp4` :
    resourceType === "reference_videos" ? `reference_videos/${resourceId}.mp4` :
    resourceType === "audio" ? `audio/segment_${resourceId}.wav` :
    resourceType === "characters" ? `characters/${resourceId}.png` :
    // 衍生的 resource id 本身是 `本体/衍生`，两段原样成为路径层级。
    resourceType === "character_derivatives" ? `characters/derivatives/${resourceId}.png` :
    resourceType === "scenes" ? `scenes/${resourceId}.png` :
    resourceType === "grids" ? `grids/${resourceId}.png` :
    `props/${resourceId}.png`;
  const resourceFp = useProjectsStore((s) => s.getAssetFingerprint(resourcePath));
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const [open, setOpen] = useState(false);
  const [panelPos, setPanelPos] = useState<{ top: number; left: number } | null>(null);
  const [versions, setVersions] = useState<VersionInfo[]>([]);
  const [currentVersion, setCurrentVersion] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadedOnce, setLoadedOnce] = useState(false);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [restoringVersion, setRestoringVersion] = useState<number | null>(null);

  // Reset version list when the underlying resource changes so it's re-fetched
  // on next open. Do NOT close the panel — if it's open and a new generation
  // completes, the user should stay in context and see the refreshed list.
  useEffect(() => {
    // 底层资源切换时重置版本列表与加载状态，等下次打开面板时重新拉取
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setVersions([]);
    setCurrentVersion(0);
    setLoading(false);
    setLoadedOnce(false);
    setSelectedVersion(null);
    setRestoringVersion(null);
  }, [resourceFp, projectName, resourceId, resourceType]);

  // Fetch versions once when panel first opens
  useEffect(() => {
    if (!open || loadedOnce || !resourceId) return;
    void loadVersions();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- loadVersions 是组件内普通函数，无法稳定化；加入 deps 会导致每次渲染重复触发
  }, [open, loadedOnce, resourceId]);

  async function loadVersions() {
    setLoading(true);
    try {
      const data = await API.getVersions(projectName, resourceType, resourceId);
      setVersions(data.versions);
      setCurrentVersion(data.current_version);
      setLoadedOnce(true);
    } catch {
      setVersions([]);
    } finally {
      setLoading(false);
    }
  }

  async function handleRestore(version: number) {
    // disabled 是响应式的 restoringVersion/busy：面板打开期间资源转为占用中时随之更新，
    // 这里兜底防止禁用态生效前的一次点击仍发出恢复请求。
    if (busy || restoringVersion !== null) return;
    // 渲染快照之外再做一次新鲜读：状态已变、渲染未到的窗口里按钮仍可点。
    if (checkBusy?.()) {
      useAppStore.getState().pushToast(t("version_restore_busy_hint"), "error");
      return;
    }
    setRestoringVersion(version);
    onRestoringChange?.(true);
    try {
      const result = await API.restoreVersion(projectName, resourceType, resourceId, version);
      if (result.asset_fingerprints) {
        useProjectsStore.getState().updateAssetFingerprints(result.asset_fingerprints);
      }
      await onRestore?.(version);
      await loadVersions();
      setSelectedVersion(version);
      useAppStore.getState().pushToast(t("switched_to_version", { version }), "success");
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(t("switch_version_failed", { message: errMsg(err) }), "error");
    } finally {
      setRestoringVersion(null);
      onRestoringChange?.(false);
    }
  }

  // Close the panel
  const close = useCallback(() => setOpen(false), []);

  // Compute ideal top position given the trigger rect and panel height
  const computeTop = useCallback(
    (triggerRect: DOMRect, panelHeight: number) => {
      const GAP = 8;
      return triggerRect.bottom + GAP + panelHeight > window.innerHeight
        ? Math.max(GAP, triggerRect.top - GAP - panelHeight)
        : triggerRect.bottom + GAP;
    },
    [],
  );

  // Re-position panel after it mounts or resizes
  const panelCallbackRef = useCallback(
    (node: HTMLDivElement | null) => {
      panelRef.current = node;
      if (!node || !triggerRef.current) return;
      const rect = triggerRef.current.getBoundingClientRect();
      const top = computeTop(rect, node.offsetHeight);
      setPanelPos((prev) =>
        prev && Math.abs(prev.top - top) > 1 ? { ...prev, top } : prev,
      );
    },
    [computeTop],
  );

  // Position panel & register dismiss listeners
  useEffect(() => {
    if (!open || !triggerRef.current) {
      setPanelPos(null);
      return;
    }
    const rect = triggerRef.current.getBoundingClientRect();
    // Use estimated height for initial placement; panelCallbackRef corrects after mount
    const top = computeTop(rect, 320);
    setPanelPos({ top, left: rect.right });

    // Close on scroll (any scrollable ancestor)
    const scrollParents = getScrollParents(triggerRef.current);
    for (const sp of scrollParents) {
      sp.addEventListener("scroll", close, { passive: true, once: true });
    }

    // Close on click outside
    function onMouseDown(e: MouseEvent) {
      if (
        panelRef.current?.contains(e.target as Node) ||
        triggerRef.current?.contains(e.target as Node)
      )
        return;
      close();
    }
    document.addEventListener("mousedown", onMouseDown);

    return () => {
      for (const sp of scrollParents) sp.removeEventListener("scroll", close);
      document.removeEventListener("mousedown", onMouseDown);
    };
  }, [open, close, computeTop]);

  if (!resourceId) return null;

  // Derive the selected version's full info from the latest `versions` array
  const selectedInfo =
    selectedVersion != null
      ? versions.find((v) => v.version === selectedVersion) ?? null
      : null;

  return (
    <div>
      {iconOnly ? (
        <button
          ref={triggerRef}
          type="button"
          onClick={() => setOpen((prev) => !prev)}
          title={t("version_mgmt")}
          aria-label={t("version_mgmt")}
          aria-haspopup="dialog"
          aria-expanded={open}
          className="focus-ring inline-flex h-7 w-7 items-center justify-center rounded-md transition-colors hover:bg-[oklch(1_0_0_/_0.05)]"
          style={{ color: "var(--color-text-3)" }}
        >
          <History className="h-3.5 w-3.5" />
        </button>
      ) : (
        <button
          ref={triggerRef}
          type="button"
          onClick={() => setOpen((prev) => !prev)}
          aria-haspopup="dialog"
          aria-expanded={open}
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200"
        >
          <History className="h-3 w-3" />
          <span>{t("version_mgmt")}</span>
          {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        </button>
      )}

      {open &&
        panelPos &&
        createPortal(
          <div
            ref={panelCallbackRef}
            style={{
              position: "fixed",
              top: panelPos.top,
              left: panelPos.left,
              transform: "translateX(-100%)",
            }}
            className="z-[9999] w-64 rounded-xl border border-gray-700 bg-gray-900/95 p-3 shadow-2xl shadow-black/40 backdrop-blur"
          >
            {loading ? (
              <span className="text-xs text-gray-500">{t("common:loading")}</span>
            ) : versions.length === 0 ? (
              <div className="space-y-1">
                <p className="text-[11px] font-medium text-gray-300">{t("no_history")}</p>
                <p className="text-[11px] leading-5 text-gray-500">
                  {t("history_hint")}
                </p>
              </div>
            ) : (
              <div className="space-y-3">
                {/* Header */}
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-500">
                    {t("history_versions")}
                  </span>
                  {currentVersion > 0 && (
                    <span className="rounded-full border border-indigo-500/30 bg-indigo-500/10 px-2 py-0.5 text-[10px] font-medium text-indigo-200">
                      {t("current_version", { version: currentVersion })}
                    </span>
                  )}
                </div>

                {/* Version pills */}
                <div className="flex flex-wrap gap-1.5">
                  {versions.map((v) => {
                    const isCurrent = v.is_current;
                    const isSelected = selectedVersion === v.version;
                    return (
                      <button
                        key={v.version}
                        type="button"
                        onClick={() =>
                          setSelectedVersion((prev) =>
                            prev === v.version ? null : v.version,
                          )
                        }
                        className={
                          "rounded-full px-2.5 py-1 text-[10px] font-medium transition-colors " +
                          (isSelected
                            ? "bg-indigo-600 text-white ring-1 ring-indigo-400"
                            : isCurrent
                              ? "bg-indigo-500/15 text-indigo-300 ring-1 ring-indigo-500/30"
                              : "bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-white")
                        }
                      >
                        v{v.version}
                      </button>
                    );
                  })}
                </div>

                {!selectedInfo && (
                  <p className="text-[10px] leading-4 text-gray-400">
                    {t("version_click_hint")}
                  </p>
                )}

                {/* Preview area */}
                {selectedInfo && (
                  <div className="rounded-xl border border-gray-700 bg-gray-950/80 p-2.5">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <span className="flex items-center gap-1.5 text-[11px] font-medium text-gray-200">
                        v{selectedInfo.version}
                        {selectedInfo.source === "image_edit" && (
                          <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[9px] font-medium text-amber-200">
                            {t("version_image_edit_badge")}
                          </span>
                        )}
                        <span className="text-[10px] font-normal text-gray-500">
                          {selectedInfo.created_at}
                        </span>
                      </span>
                      {selectedInfo.is_current ? (
                        <span className="shrink-0 rounded-full bg-indigo-500/10 px-2 py-0.5 text-[10px] font-medium text-indigo-300">
                          {t("current_version_badge")}
                        </span>
                      ) : !readOnly && selectedInfo.restorable !== false ? (
                        <button
                          type="button"
                          disabled={restoringVersion !== null || busy}
                          onClick={() => void handleRestore(selectedInfo.version)}
                          title={busy ? t("version_restore_busy_hint") : undefined}
                          className="shrink-0 rounded-full bg-indigo-600 px-2.5 py-0.5 text-[10px] font-medium text-white transition-colors hover:bg-indigo-500 disabled:opacity-50"
                        >
                          {restoringVersion === selectedInfo.version ? t("switching_version") : t("switch_to_version")}
                        </button>
                      ) : null}
                    </div>

                    {/* Media preview */}
                    {selectedInfo.file_url &&
                      (resourceType === "videos" || resourceType === "reference_videos" ? (
                        <div className="mb-2 aspect-video w-full overflow-hidden rounded-lg border border-gray-800 bg-black">
                          {selectedInfo.presentation_available !== true ? (
                            // eslint-disable-next-line jsx-a11y/media-has-caption -- 无法进入共享成片读取器的历史视频仅展示原始媒体
                            <video
                              src={selectedInfo.file_url}
                              aria-label={t("version_preview_alt", { version: selectedInfo.version })}
                              className="h-full w-full object-contain"
                              controls
                              playsInline
                              preload="none"
                            />
                          ) : (
                            <PresentationPlayer
                              key={`${resourceType}:${resourceId}:${selectedInfo.version}`}
                              projectName={projectName}
                              resourceType={resourceType}
                              resourceId={resourceId}
                              videoVersion={selectedInfo.version}
                            />
                          )}
                        </div>
                      ) : resourceType === "audio" ? (
                        // eslint-disable-next-line jsx-a11y/media-has-caption -- 历史旁白的文字记录显示在同一预览卡片
                        <audio
                          src={selectedInfo.file_url}
                          aria-label={t("version_audio_preview_label", { version: selectedInfo.version })}
                          className="mb-2 h-9 w-full"
                          controls
                          preload="metadata"
                        />
                      ) : (
                        <div
                          className={`mb-2 flex w-full items-center justify-center rounded-lg border border-gray-800 bg-gray-900/70 p-2 ${getImagePreviewHeightClass(resourceType)}`}
                        >
                          <img
                            src={selectedInfo.file_url}
                            alt={t("version_preview_alt", { version: selectedInfo.version })}
                            className="max-h-full w-full object-contain"
                          />
                        </div>
                      ))}

                    {resourceType === "audio" && selectedInfo.file_url && (
                      <a
                        href={selectedInfo.file_url}
                        download
                        className="mb-2 inline-flex items-center gap-1 rounded-md border border-gray-700 px-2 py-1 text-[10px] font-medium text-gray-300 hover:bg-gray-800 hover:text-white"
                      >
                        <Download className="h-3 w-3" aria-hidden />
                        {t("version_download_audio")}
                      </a>
                    )}

                    {/* Prompt text */}
                    <p className="line-clamp-4 text-[11px] leading-5 text-gray-400">
                      {selectedInfo.prompt ||
                        (selectedInfo.source === "manual_upload"
                          ? t("version_manual_upload")
                          : t("version_no_notes"))}
                    </p>


                  </div>
                )}

              </div>
            )}
          </div>,
          document.body,
        )}
    </div>
  );
}
