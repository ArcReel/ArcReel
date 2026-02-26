import { useState, useEffect } from "react";
import { API, type VersionInfo } from "@/api";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface VersionTimeMachineProps {
  projectName: string;
  resourceType: "storyboards" | "videos" | "characters" | "clues";
  resourceId: string;
  currentVersion?: number;
  onRestore?: (version: number) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Micro timeline showing version history of a resource (storyboard/video/etc).
 *
 * Renders as a compact horizontal bar of version pills. The current version is
 * highlighted with an indigo accent ring. Clicking a non-current version
 * triggers a restore and re-fetches the version list.
 */
export function VersionTimeMachine({
  projectName,
  resourceType,
  resourceId,
  onRestore,
}: VersionTimeMachineProps) {
  const [versions, setVersions] = useState<VersionInfo[]>([]);
  const [currentVersion, setCurrentVersion] = useState<number>(0);
  const [loading, setLoading] = useState(false);
  const [hoveredVersion, setHoveredVersion] = useState<number | null>(null);

  useEffect(() => {
    if (!resourceId) return;
    loadVersions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectName, resourceType, resourceId]);

  async function loadVersions() {
    setLoading(true);
    try {
      const data = await API.getVersions(projectName, resourceType, resourceId);
      setVersions(data.versions);
      setCurrentVersion(data.current_version);
    } catch {
      // Silently ignore — version info is non-critical
    }
    setLoading(false);
  }

  async function handleRestore(version: number) {
    try {
      await API.restoreVersion(projectName, resourceType, resourceId, version);
      await loadVersions();
      onRestore?.(version);
    } catch {
      // Restore failed — leave state as-is
    }
  }

  // No resource yet — nothing to show
  if (!resourceId) return null;

  // No versions and not loading — show placeholder text
  if (versions.length === 0 && !loading) {
    return (
      <div className="text-xs text-gray-600 py-1">
        暂无历史版本
      </div>
    );
  }

  // Loading skeleton dots
  if (loading && versions.length === 0) {
    return (
      <div className="flex items-center gap-1 py-1.5">
        {[1, 2, 3].map((i) => (
          <span
            key={i}
            className="h-4 w-6 rounded-full bg-gray-800 animate-pulse"
          />
        ))}
      </div>
    );
  }

  return (
    <div className="flex items-center gap-1 px-2 py-1.5 text-xs">
      {versions.map((v) => {
        const isCurrent = v.is_current;
        const isHovered = hoveredVersion === v.version;

        // Format tooltip content
        const tooltipLines: string[] = [];
        if (v.created_at) tooltipLines.push(v.created_at);

        return (
          <div key={v.version} className="relative">
            <button
              type="button"
              className={
                "rounded-full px-1.5 py-0.5 text-[10px] font-medium transition-colors " +
                (isCurrent
                  ? "bg-indigo-600 text-white ring-1 ring-indigo-400"
                  : "bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-gray-200")
              }
              onClick={() => {
                if (!isCurrent) handleRestore(v.version);
              }}
              onMouseEnter={() => setHoveredVersion(v.version)}
              onMouseLeave={() => setHoveredVersion(null)}
              title={tooltipLines.join("\n")}
            >
              v{v.version}
            </button>

            {/* Hover tooltip with more detail */}
            {isHovered && !isCurrent && (
              <div className="absolute bottom-full left-1/2 z-20 mb-1.5 -translate-x-1/2 whitespace-nowrap rounded bg-gray-800 px-2 py-1 text-[10px] text-gray-300 shadow-lg ring-1 ring-gray-700">
                点击还原到 v{v.version}
              </div>
            )}
          </div>
        );
      })}

      {/* Current version label */}
      {currentVersion > 0 && (
        <span className="ml-1 text-[10px] text-gray-600">
          (当前 v{currentVersion})
        </span>
      )}
    </div>
  );
}
