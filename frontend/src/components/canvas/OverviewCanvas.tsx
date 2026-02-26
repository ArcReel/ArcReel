import type { ProjectData } from "@/types";
import { WelcomeCanvas } from "./WelcomeCanvas";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface OverviewCanvasProps {
  projectName: string;
  projectData: ProjectData | null;
}

// ---------------------------------------------------------------------------
// OverviewCanvas — project overview page showing metadata and progress
// ---------------------------------------------------------------------------

export function OverviewCanvas({ projectName, projectData }: OverviewCanvasProps) {
  if (!projectData) {
    return (
      <div className="flex h-full items-center justify-center text-gray-500">
        加载项目数据中...
      </div>
    );
  }

  // Show welcome page when project has no overview and no episodes yet
  if (!projectData.overview && projectData.episodes.length === 0) {
    return <WelcomeCanvas projectName={projectName} />;
  }

  const progress = projectData.status?.progress;
  const overview = projectData.overview;

  return (
    <div className="h-full overflow-y-auto">
      <div className="p-6 space-y-6">
        {/* ---- Title ---- */}
        <div>
          <h1 className="text-2xl font-bold text-gray-100">{projectData.title}</h1>
          <p className="text-sm text-gray-400 mt-1">
            {projectData.content_mode === "narration"
              ? "说书+画面模式"
              : "剧集动画模式"}{" "}
            · {projectData.style || "未设置风格"}
          </p>
        </div>

        {/* ---- Overview synopsis ---- */}
        {overview && (
          <div className="rounded-xl border border-gray-800 bg-gray-900 p-4 space-y-3">
            <h3 className="text-sm font-semibold text-gray-300">项目概述</h3>
            <p className="text-sm text-gray-400">{overview.synopsis}</p>
            <div className="flex gap-4 text-xs text-gray-500">
              <span>题材: {overview.genre}</span>
              <span>主题: {overview.theme}</span>
            </div>
          </div>
        )}

        {/* ---- Progress bars ---- */}
        {progress && (
          <div className="grid grid-cols-2 gap-3">
            {(["characters", "clues", "storyboards", "videos"] as const).map(
              (key) => {
                const cat = progress[key];
                const pct =
                  cat.total > 0
                    ? Math.round((cat.completed / cat.total) * 100)
                    : 0;
                const labels: Record<string, string> = {
                  characters: "角色",
                  clues: "线索",
                  storyboards: "分镜",
                  videos: "视频",
                };
                return (
                  <div
                    key={key}
                    className="rounded-lg border border-gray-800 bg-gray-900 p-3"
                  >
                    <div className="flex justify-between text-xs mb-1">
                      <span className="text-gray-400">{labels[key]}</span>
                      <span className="text-gray-300">
                        {cat.completed}/{cat.total}
                      </span>
                    </div>
                    <div className="h-1.5 rounded-full bg-gray-800 overflow-hidden">
                      <div
                        className="h-full rounded-full bg-indigo-500"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                );
              },
            )}
          </div>
        )}

        {/* ---- Episodes list ---- */}
        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-gray-300">剧集</h3>
          {projectData.episodes.length === 0 ? (
            <p className="text-sm text-gray-500">
              暂无剧集。使用 AI 助手生成剧本。
            </p>
          ) : (
            projectData.episodes.map((ep) => (
              <div
                key={ep.episode}
                className="flex items-center gap-3 rounded-lg border border-gray-800 bg-gray-900 px-4 py-2.5"
              >
                <span className="font-mono text-xs text-gray-400">
                  E{ep.episode}
                </span>
                <span className="text-sm text-gray-200">{ep.title}</span>
                <span className="ml-auto text-xs text-gray-500">
                  {ep.scenes_count ?? "?"} 片段 · {ep.status ?? "draft"}
                </span>
              </div>
            ))
          )}
        </div>

        {/* Bottom spacer */}
        <div className="h-8" />
      </div>
    </div>
  );
}
