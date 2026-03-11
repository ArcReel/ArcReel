import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation } from "wouter";
import { Loader2, Plus, FolderOpen, Upload, AlertTriangle, Settings } from "lucide-react";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { CreateProjectModal } from "./CreateProjectModal";
import { OpenClawModal } from "./OpenClawModal";
import type { ImportConflictPolicy, ProjectSummary, ProjectStatus } from "@/types";

interface ImportConflictDialogProps {
  projectName: string;
  importing: boolean;
  onCancel: () => void;
  onConfirm: (policy: Extract<ImportConflictPolicy, "rename" | "overwrite">) => void;
}

function ImportConflictDialog({
  projectName,
  importing,
  onCancel,
  onConfirm,
}: ImportConflictDialogProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 px-4">
      <div className="w-full max-w-md rounded-2xl border border-amber-400/20 bg-gray-900 p-6 shadow-2xl shadow-black/40">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 rounded-full bg-amber-400/10 p-2 text-amber-300">
            <AlertTriangle className="h-5 w-5" />
          </div>
          <div className="space-y-2">
            <h2 className="text-lg font-semibold text-gray-100">检测到项目编号重复</h2>
            <p className="text-sm leading-6 text-gray-400">
              导入包准备使用的项目编号
              <span className="mx-1 rounded bg-gray-800 px-1.5 py-0.5 font-mono text-gray-200">
                {projectName}
              </span>
              已存在。你可以覆盖现有项目，或自动重命名后继续导入。
            </p>
          </div>
        </div>

        <div className="mt-5 grid gap-3">
          <button
            type="button"
            onClick={() => onConfirm("overwrite")}
            disabled={importing}
            aria-label="覆盖现有项目"
            className="flex w-full items-center justify-between rounded-xl border border-red-400/25 bg-red-500/10 px-4 py-3 text-left text-sm text-red-100 transition-colors hover:border-red-300/40 hover:bg-red-500/15 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <span>
              <span className="block font-medium">覆盖现有项目</span>
              <span className="mt-1 block text-xs text-red-200/80">
                使用导入包内容替换现有项目编号对应的数据
              </span>
            </span>
            {importing && <Loader2 className="h-4 w-4 animate-spin" />}
          </button>

          <button
            type="button"
            onClick={() => onConfirm("rename")}
            disabled={importing}
            aria-label="自动重命名导入"
            className="flex w-full items-center justify-between rounded-xl border border-indigo-400/25 bg-indigo-500/10 px-4 py-3 text-left text-sm text-indigo-100 transition-colors hover:border-indigo-300/40 hover:bg-indigo-500/15 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <span>
              <span className="block font-medium">自动重命名导入</span>
              <span className="mt-1 block text-xs text-indigo-200/80">
                保留现有项目，新导入项目自动生成新的内部编号
              </span>
            </span>
            {importing && <Loader2 className="h-4 w-4 animate-spin" />}
          </button>
        </div>

        <div className="mt-5 flex justify-end">
          <button
            type="button"
            onClick={onCancel}
            disabled={importing}
            className="rounded-lg border border-gray-700 px-4 py-2 text-sm text-gray-300 transition-colors hover:border-gray-500 hover:text-white disabled:cursor-not-allowed disabled:opacity-60"
          >
            取消
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Phase display helpers
// ---------------------------------------------------------------------------

const PHASE_LABELS: Record<string, string> = {
  setup: "准备中",
  worldbuilding: "世界观",
  scripting: "剧本创作",
  production: "制作中",
  completed: "已完成",
};

// ---------------------------------------------------------------------------
// ProjectCard — single project entry
// ---------------------------------------------------------------------------

function ProjectCard({ project }: { project: ProjectSummary }) {
  const [, navigate] = useLocation();
  const status = project.status;
  const hasStatus = status && "current_phase" in status;

  const pct = hasStatus ? Math.round((status as ProjectStatus).phase_progress * 100) : 0;
  const phase = hasStatus ? (status as ProjectStatus).current_phase : "";
  const phaseLabel = PHASE_LABELS[phase] ?? phase;
  const characters = hasStatus ? (status as ProjectStatus).characters : null;
  const clues = hasStatus ? (status as ProjectStatus).clues : null;
  const summary = hasStatus ? (status as ProjectStatus).episodes_summary : null;

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
            src={project.thumbnail}
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
          {project.style || "未设置风格"}
          {phaseLabel ? ` · ${phaseLabel}` : ""}
        </p>
      </div>

      {/* Progress bar */}
      <div>
        <div className="flex justify-between text-xs text-gray-500 mb-1">
          <span>{phaseLabel || "进度"}</span>
          <span>{pct}%</span>
        </div>
        <div className="h-1.5 rounded-full bg-gray-800 overflow-hidden">
          <div
            className="h-full rounded-full bg-indigo-600 transition-all"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      {/* Characters & Clues — always shown */}
      {(characters || clues) && (
        <div className="flex gap-3 text-xs text-gray-500">
          {characters && (
            <span>人物 {characters.completed}/{characters.total}</span>
          )}
          {clues && (
            <span>线索 {clues.completed}/{clues.total}</span>
          )}
        </div>
      )}

      {/* Episodes summary */}
      {summary && summary.total > 0 && (
        <div className="text-xs text-gray-500">
          {summary.total} 集
          {summary.scripted > 0 && ` · ${summary.scripted} 集剧本完成`}
          {summary.in_production > 0 && ` · ${summary.in_production} 集制作中`}
          {summary.completed > 0 && ` · ${summary.completed} 集已完成`}
        </div>
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// ProjectsPage — project list with create button
// ---------------------------------------------------------------------------

export function ProjectsPage() {
  const [, navigate] = useLocation();
  const { projects, projectsLoading, showCreateModal, setProjects, setProjectsLoading, setShowCreateModal } =
    useProjectsStore();
  const [importingProject, setImportingProject] = useState(false);
  const [pendingImportFile, setPendingImportFile] = useState<File | null>(null);
  const [conflictProjectName, setConflictProjectName] = useState<string | null>(null);
  const [showOpenClaw, setShowOpenClaw] = useState(false);
  const importInputRef = useRef<HTMLInputElement>(null);
  const isConfigComplete = useConfigStatusStore((s) => s.isComplete);
  const fetchConfigStatus = useConfigStatusStore((s) => s.fetch);

  const loadProjects = useCallback(async () => {
    setProjectsLoading(true);
    try {
      const res = await API.listProjects();
      setProjects(res.projects);
    } catch {
      // silently fail — user can retry
    } finally {
      setProjectsLoading(false);
    }
  }, [setProjects, setProjectsLoading]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      if (cancelled) return;
      await loadProjects();
    })();
    return () => {
      cancelled = true;
    };
  }, [loadProjects]);

  useEffect(() => {
    void fetchConfigStatus();
  }, [fetchConfigStatus]);

  const finishImport = useCallback(
    async (
      file: File,
      policy: ImportConflictPolicy,
      options?: { keepConflictDialog?: boolean },
    ) => {
      setImportingProject(true);
      try {
        const result = await API.importProject(file, policy);
        setPendingImportFile(null);
        setConflictProjectName(null);
        await loadProjects();

        useAppStore.getState().pushToast(
          `项目 "${result.project.title || result.project_name}" 已导入`,
          "success"
        );
        if (result.warnings.length > 0) {
          useAppStore.getState().pushToast(
            `导入警告: ${result.warnings[0]}`,
            "warning"
          );
        }

        navigate(`/app/projects/${result.project_name}`);
      } catch (err) {
        const error = err as Error & {
          status?: number;
          detail?: string;
          errors?: string[];
          conflict_project_name?: string;
        };

        if (
          error.status === 409 &&
          error.conflict_project_name &&
          policy === "prompt"
        ) {
          setPendingImportFile(file);
          setConflictProjectName(error.conflict_project_name);
          return;
        }

        if (!options?.keepConflictDialog) {
          setPendingImportFile(null);
          setConflictProjectName(null);
        }

        const fragments = [
          error.detail || error.message || "导入失败",
          ...(error.errors ?? []).slice(0, 2),
        ].filter(Boolean);

        useAppStore
          .getState()
          .pushToast(`导入失败: ${fragments.join("；")}`, "error");
      } finally {
        setImportingProject(false);
      }
    },
    [loadProjects, navigate],
  );

  const handleImport = useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      event.target.value = "";
      if (!file || importingProject) return;

      await finishImport(file, "prompt");
    },
    [finishImport, importingProject],
  );

  const handleResolveConflict = useCallback(
    async (policy: Extract<ImportConflictPolicy, "rename" | "overwrite">) => {
      if (!pendingImportFile) return;
      await finishImport(pendingImportFile, policy, { keepConflictDialog: true });
    },
    [finishImport, pendingImportFile],
  );

  const handleCancelConflict = useCallback(() => {
    if (importingProject) return;
    setPendingImportFile(null);
    setConflictProjectName(null);
  }, [importingProject]);

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      {/* Header */}
      <header className="border-b border-gray-800 px-6 py-4">
        <div className="mx-auto flex max-w-6xl items-center justify-between">
          <h1 className="flex items-center gap-2 text-xl font-bold">
            <img src="/android-chrome-192x192.png" alt="ArcReel" className="h-7 w-7" />
            <span className="text-indigo-400">
              ArcReel
            </span>
            <span className="ml-1 text-gray-400 font-normal text-base">项目</span>
          </h1>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => importInputRef.current?.click()}
              disabled={importingProject}
              className="inline-flex items-center gap-1.5 rounded-lg border border-gray-700 bg-gray-900 px-4 py-2 text-sm font-medium text-gray-200 transition-colors hover:border-gray-500 hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {importingProject ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Upload className="h-4 w-4" />
              )}
              {importingProject ? "导入中..." : "导入 ZIP"}
            </button>
            <button
              type="button"
              onClick={() => setShowCreateModal(true)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 transition-colors cursor-pointer"
            >
              <Plus className="h-4 w-4" />
              新建项目
            </button>
            <div className="ml-1 flex items-center gap-1 border-l border-gray-800 pl-3">
              <button
                type="button"
                onClick={() => setShowOpenClaw(true)}
                className="rounded-md px-2.5 py-1.5 text-sm text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200"
                title="OpenClaw 集成"
                aria-label="OpenClaw 集成指南"
              >
                🦞
              </button>
              <button
                type="button"
                onClick={() => navigate("/app/settings")}
                className="relative rounded-md p-1.5 text-gray-400 transition-colors hover:bg-gray-800 hover:text-gray-200"
                title="系统配置"
                aria-label="系统配置"
              >
                <Settings className="h-4 w-4" />
                {!isConfigComplete && (
                  <span className="absolute right-0.5 top-0.5 h-2 w-2 rounded-full bg-rose-500" aria-label="配置不完整" />
                )}
              </button>
            </div>
          </div>
        </div>
        <input
          ref={importInputRef}
          type="file"
          accept=".zip,application/zip"
          onChange={handleImport}
          className="hidden"
        />
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
            <p className="text-sm mt-1">点击右上角「新建项目」或「导入 ZIP」开始创作</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {projects.map((p) => (
              <ProjectCard key={p.name} project={p} />
            ))}
          </div>
        )}
      </main>

      {/* Create project modal */}
      {showCreateModal && <CreateProjectModal />}
      {conflictProjectName !== null && pendingImportFile !== null && (
        <ImportConflictDialog
          projectName={conflictProjectName}
          importing={importingProject}
          onCancel={handleCancelConflict}
          onConfirm={handleResolveConflict}
        />
      )}
      {showOpenClaw && <OpenClawModal onClose={() => setShowOpenClaw(false)} />}
    </div>
  );
}
