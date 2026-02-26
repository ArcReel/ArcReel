import { useEffect } from "react";
import { useLocation } from "wouter";
import { Loader2, Plus, FolderOpen } from "lucide-react";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectSummary } from "@/types";

// ---------------------------------------------------------------------------
// ProjectCard — single project entry
// ---------------------------------------------------------------------------

function ProjectCard({ project }: { project: ProjectSummary }) {
  const [, navigate] = useLocation();
  const progress = project.progress;
  const totalItems =
    progress.characters.total +
    progress.clues.total +
    progress.storyboards.total +
    progress.videos.total;
  const completedItems =
    progress.characters.completed +
    progress.clues.completed +
    progress.storyboards.completed +
    progress.videos.completed;
  const pct = totalItems > 0 ? Math.round((completedItems / totalItems) * 100) : 0;

  return (
    <button
      type="button"
      onClick={() => navigate(`/app/projects/${project.name}`)}
      className="flex flex-col gap-3 rounded-xl border border-gray-800 bg-gray-900 p-5 text-left transition-colors hover:border-indigo-500/50 hover:bg-gray-800/50 cursor-pointer"
    >
      {/* Thumbnail or placeholder */}
      <div className="aspect-video w-full overflow-hidden rounded-lg bg-gray-800">
        {project.thumbnail ? (
          <img
            src={API.getFileUrl(project.name, project.thumbnail)}
            alt={project.title}
            className="h-full w-full object-cover"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-gray-600">
            <FolderOpen className="h-10 w-10" />
          </div>
        )}
      </div>

      {/* Info */}
      <div>
        <h3 className="font-semibold text-gray-100 truncate">{project.title}</h3>
        <p className="text-xs text-gray-500 mt-0.5">
          {project.style || "未设置风格"} · {project.current_phase}
        </p>
      </div>

      {/* Progress bar */}
      <div>
        <div className="flex justify-between text-xs text-gray-500 mb-1">
          <span>进度</span>
          <span>{pct}%</span>
        </div>
        <div className="h-1.5 rounded-full bg-gray-800 overflow-hidden">
          <div
            className="h-full rounded-full bg-gradient-to-r from-indigo-500 to-fuchsia-500 transition-all"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// ProjectsPage — project list with create button
// ---------------------------------------------------------------------------

export function ProjectsPage() {
  const { projects, projectsLoading, setProjects, setProjectsLoading } =
    useProjectsStore();
  const [, navigate] = useLocation();

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setProjectsLoading(true);
      try {
        const res = await API.listProjects();
        if (!cancelled) setProjects(res.projects);
      } catch {
        // silently fail — user can retry
      } finally {
        if (!cancelled) setProjectsLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [setProjects, setProjectsLoading]);

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      {/* Header */}
      <header className="border-b border-gray-800 px-6 py-4">
        <div className="mx-auto flex max-w-6xl items-center justify-between">
          <h1 className="text-xl font-bold">
            <span className="bg-gradient-to-r from-indigo-400 to-fuchsia-400 bg-clip-text text-transparent">
              ArcReel
            </span>
            <span className="ml-2 text-gray-400 font-normal text-base">项目</span>
          </h1>
          <button
            type="button"
            onClick={() => navigate("/app/projects/new")}
            className="inline-flex items-center gap-1.5 rounded-lg bg-gradient-to-r from-indigo-500 to-fuchsia-500 px-4 py-2 text-sm font-medium text-white hover:from-indigo-400 hover:to-fuchsia-400 transition-colors cursor-pointer"
          >
            <Plus className="h-4 w-4" />
            新建项目
          </button>
        </div>
      </header>

      {/* Content */}
      <main className="mx-auto max-w-6xl px-6 py-8">
        {projectsLoading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="h-6 w-6 animate-spin text-indigo-400" />
            <span className="ml-2 text-gray-400">加载项目列表...</span>
          </div>
        ) : projects.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-gray-500">
            <FolderOpen className="h-16 w-16 mb-4" />
            <p className="text-lg">暂无项目</p>
            <p className="text-sm mt-1">点击右上角「新建项目」开始创作</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {projects.map((p) => (
              <ProjectCard key={p.name} project={p} />
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
