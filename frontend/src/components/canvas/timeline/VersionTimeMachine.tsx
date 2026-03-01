import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, History } from "lucide-react";
import { API, type VersionInfo } from "@/api";
import { useAppStore } from "@/stores/app-store";

interface VersionTimeMachineProps {
  projectName: string;
  resourceType: "storyboards" | "videos" | "characters" | "clues";
  resourceId: string;
  onRestore?: (version: number) => void | Promise<void>;
}

function getImagePreviewHeightClass(
  resourceType: VersionTimeMachineProps["resourceType"],
): string {
  if (resourceType === "characters") {
    return "h-80";
  }

  if (resourceType === "clues") {
    return "h-56";
  }

  return "h-64";
}

export function VersionTimeMachine({
  projectName,
  resourceType,
  resourceId,
  onRestore,
}: VersionTimeMachineProps) {
  const mediaRevision = useAppStore((s) => s.mediaRevision);
  const [expanded, setExpanded] = useState(false);
  const [versions, setVersions] = useState<VersionInfo[]>([]);
  const [currentVersion, setCurrentVersion] = useState<number>(0);
  const [loading, setLoading] = useState(false);
  const [restoringVersion, setRestoringVersion] = useState<number | null>(null);
  const [loadedOnce, setLoadedOnce] = useState(false);
  const [previewedVersion, setPreviewedVersion] = useState<VersionInfo | null>(null);

  useEffect(() => {
    setVersions([]);
    setCurrentVersion(0);
    setLoading(false);
    setRestoringVersion(null);
    setLoadedOnce(false);
    setPreviewedVersion(null);
  }, [mediaRevision, projectName, resourceId, resourceType]);

  useEffect(() => {
    if (!expanded || loadedOnce || !resourceId) return;
    void loadVersions();
  }, [expanded, loadedOnce, resourceId]);

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
    setPreviewedVersion(null);
    try {
      await API.restoreVersion(projectName, resourceType, resourceId, version);
      await onRestore?.(version);
      await loadVersions();
      useAppStore.getState().pushToast(`已切换到 v${version}`, "success");
    } catch (err) {
      useAppStore
        .getState()
        .pushToast(`切换版本失败: ${(err as Error).message}`, "error");
    } finally {
      setRestoringVersion(null);
    }
  }

  if (!resourceId) return null;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200"
      >
        <History className="h-3 w-3" />
        <span>版本管理</span>
        {expanded ? (
          <ChevronDown className="h-3 w-3" />
        ) : (
          <ChevronRight className="h-3 w-3" />
        )}
      </button>

      {expanded && (
        <div className="absolute right-0 top-full z-20 mt-2 w-72 max-w-[calc(100vw-2rem)] rounded-xl border border-gray-700 bg-gray-900/95 p-3 shadow-2xl shadow-black/40 backdrop-blur">
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

              <div className="flex flex-wrap gap-2">
                {versions.map((version) => {
                  const isCurrent = version.is_current;
                  const isRestoring = restoringVersion === version.version;

                  return (
                    <button
                      key={version.version}
                      type="button"
                      onClick={() => {
                        if (!isCurrent && !isRestoring) {
                          void handleRestore(version.version);
                        }
                      }}
                      onMouseEnter={() => setPreviewedVersion(version)}
                      onMouseLeave={() =>
                        setPreviewedVersion((current) =>
                          current?.version === version.version ? null : current,
                        )
                      }
                      className={
                        "rounded-full px-2 py-1 text-[10px] font-medium transition-colors " +
                        (isCurrent
                          ? "bg-indigo-600 text-white ring-1 ring-indigo-400"
                          : "bg-gray-800 text-gray-300 hover:bg-gray-700 hover:text-white")
                      }
                    >
                      {isRestoring ? "还原中..." : `v${version.version}`}
                    </button>
                  );
                })}
              </div>

              {previewedVersion && (
                <div className="rounded-xl border border-gray-700 bg-gray-950/80 p-2.5">
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <span className="text-[11px] font-medium text-gray-200">
                      {previewedVersion.is_current
                        ? `当前版本 v${previewedVersion.version}`
                        : `版本 v${previewedVersion.version}`}
                    </span>
                    <span className="text-[10px] text-gray-500">
                      {previewedVersion.created_at}
                    </span>
                  </div>
                  {previewedVersion.file_url && (
                    resourceType === "videos" ? (
                      <video
                        src={previewedVersion.file_url}
                        className="mb-2 aspect-video w-full rounded-lg border border-gray-800 object-cover"
                        muted
                        playsInline
                        loop
                        autoPlay
                      />
                    ) : (
                      <div
                        className={`mb-2 flex w-full items-center justify-center rounded-lg border border-gray-800 bg-gray-900/70 p-2 ${getImagePreviewHeightClass(resourceType)}`}
                      >
                        <img
                          src={previewedVersion.file_url}
                          alt={`版本 v${previewedVersion.version} 预览`}
                          className="max-h-full w-full object-contain"
                        />
                      </div>
                    )
                  )}
                  <p className="line-clamp-4 text-[11px] leading-5 text-gray-400">
                    {previewedVersion.prompt || "该版本没有记录额外说明。"}
                  </p>
                </div>
              )}

              <p className="text-[11px] leading-5 text-gray-500">
                点击旧版本会直接把它切换为当前版本；悬停可预览该版本内容。
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
