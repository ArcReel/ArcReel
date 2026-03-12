import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, ChevronRight, History } from "lucide-react";
import { API, type VersionInfo } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";

interface VersionTimeMachineProps {
  projectName: string;
  resourceType: "storyboards" | "videos" | "characters" | "clues";
  resourceId: string;
  onRestore?: (version: number) => void | Promise<void>;
}

function getImagePreviewHeightClass(
  resourceType: VersionTimeMachineProps["resourceType"],
): string {
  if (resourceType === "characters") return "h-80";
  if (resourceType === "clues") return "h-56";
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
}: VersionTimeMachineProps) {
  const resourcePath =
    resourceType === "storyboards" ? `storyboards/scene_${resourceId}.png` :
    resourceType === "videos" ? `videos/scene_${resourceId}.mp4` :
    resourceType === "characters" ? `characters/${resourceId}.png` :
    `clues/${resourceId}.png`;
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
    setRestoringVersion(version);
    try {
      const result = await API.restoreVersion(projectName, resourceType, resourceId, version);
      if (result.asset_fingerprints) {
        useProjectsStore.getState().updateAssetFingerprints(result.asset_fingerprints);
      }
      await onRestore?.(version);
      await loadVersions();
      setSelectedVersion(version);
      useAppStore.getState().pushToast(`已切换到 v${version}`, "success");
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(`切换版本失败: ${(err as Error).message}`, "error");
    } finally {
      setRestoringVersion(null);
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
  }, [open, close]);

  if (!resourceId) return null;

  // Derive the selected version's full info from the latest `versions` array
  const selectedInfo =
    selectedVersion != null
      ? versions.find((v) => v.version === selectedVersion) ?? null
      : null;

  return (
    <div>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200"
      >
        <History className="h-3 w-3" />
        <span>版本管理</span>
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
      </button>

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
              <span className="text-xs text-gray-500">加载中...</span>
            ) : versions.length === 0 ? (
              <div className="space-y-1">
                <p className="text-[11px] font-medium text-gray-300">暂无历史版本</p>
                <p className="text-[11px] leading-5 text-gray-500">
                  生成或还原后，历史版本会出现在这里。
                </p>
              </div>
            ) : (
              <div className="space-y-3">
                {/* Header */}
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-semibold uppercase tracking-wider text-gray-500">
                    历史版本
                  </span>
                  {currentVersion > 0 && (
                    <span className="rounded-full border border-indigo-500/30 bg-indigo-500/10 px-2 py-0.5 text-[10px] font-medium text-indigo-200">
                      当前 v{currentVersion}
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
                    点击版本号预览，非当前版本可切换。
                  </p>
                )}

                {/* Preview area */}
                {selectedInfo && (
                  <div className="rounded-xl border border-gray-700 bg-gray-950/80 p-2.5">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <span className="text-[11px] font-medium text-gray-200">
                        v{selectedInfo.version}
                        <span className="ml-1.5 text-[10px] font-normal text-gray-500">
                          {selectedInfo.created_at}
                        </span>
                      </span>
                      {selectedInfo.is_current ? (
                        <span className="shrink-0 rounded-full bg-indigo-500/10 px-2 py-0.5 text-[10px] font-medium text-indigo-300">
                          当前
                        </span>
                      ) : (
                        <button
                          type="button"
                          disabled={restoringVersion !== null}
                          onClick={() => void handleRestore(selectedInfo.version)}
                          className="shrink-0 rounded-full bg-indigo-600 px-2.5 py-0.5 text-[10px] font-medium text-white transition-colors hover:bg-indigo-500 disabled:opacity-50"
                        >
                          {restoringVersion === selectedInfo.version ? "切换中..." : "切换到此版本"}
                        </button>
                      )}
                    </div>

                    {/* Media preview */}
                    {selectedInfo.file_url &&
                      (resourceType === "videos" ? (
                        <video
                          src={selectedInfo.file_url}
                          className="mb-2 w-full rounded-lg border border-gray-800 bg-black object-contain"
                          controls
                          playsInline
                          preload="none"
                        />
                      ) : (
                        <div
                          className={`mb-2 flex w-full items-center justify-center rounded-lg border border-gray-800 bg-gray-900/70 p-2 ${getImagePreviewHeightClass(resourceType)}`}
                        >
                          <img
                            src={selectedInfo.file_url}
                            alt={`版本 v${selectedInfo.version} 预览`}
                            className="max-h-full w-full object-contain"
                          />
                        </div>
                      ))}

                    {/* Prompt text */}
                    <p className="line-clamp-4 text-[11px] leading-5 text-gray-400">
                      {selectedInfo.prompt || "该版本没有记录额外说明。"}
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
