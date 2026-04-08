
import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation } from "wouter";
import { Loader2, Plus, FolderOpen, Upload, AlertTriangle, Settings } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { ArchiveDiagnosticsDialog } from "@/components/shared/ArchiveDiagnosticsDialog";
import { OpenClawModal } from "./OpenClawModal";
import type { ProjectStatus, ProjectSummary, ImportConflictPolicy, ImportFailureDiagnostics } from "@/types";

// ---------------------------------------------------------------------------
// Phase display helpers
// ---------------------------------------------------------------------------

function usePhaseLabels() {
  const { t } = useTranslation();
  return {
    setup: t("setup"),
    worldbuilding: t("worldbuilding"),
    scripting: t("scripting"),
    production: t("production"),
    completed: t("completed"),
  } as Record<string, string>;
}

// ---------------------------------------------------------------------------
// ProjectCard — clickable project entry
// ---------------------------------------------------------------------------

function ProjectCard({ project }: { project: ProjectSummary }) {
  const { t } = useTranslation(["common", "dashboard"]);
  const [, navigate] = useLocation();
  const status = project.status;
  const hasStatus = status && "current_phase" in status;
  const PHASE_LABELS = usePhaseLabels();

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
          {project.style || t("dashboard:style_not_set")}
          {phaseLabel ? ` · ${phaseLabel}` : ""}
        </p>
      </div>

      {/* Progress bar */}
      <div>
        <div className="flex justify-between text-xs text-gray-500 mb-1">
          <span>{phaseLabel || t("dashboard:progress")}</span>
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
            <span>{t("dashboard:characters")} {characters.completed}/{characters.total}</span>
          )}
          {clues && (
            <span>{t("dashboard:clues")} {clues.completed}/{clues.total}</span>
          )}
        </div>
      )}

      {/* Episodes summary */}
      {summary && summary.total > 0 && (
        <div className="text-xs text-gray-500">
          {summary.total} {t("dashboard:episodes")}
          {summary.scripted > 0 && ` · ${summary.scripted} ${t("dashboard:episodes_scripted")}`}
          {summary.in_production > 0 && ` · ${summary.in_production} ${t("dashboard:episodes_in_production")}`}
          {summary.completed > 0 && ` · ${summary.completed} ${t("dashboard:episodes_completed")}`}
        </div>
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// ProjectsPage — project list with create button
// ---------------------------------------------------------------------------

export function ProjectsPage() {
  const { t } = useTranslation(["common", "dashboard"]);
  const [, navigate] = useLocation();
  const { projects, projectsLoading, showCreateModal, setProjects, setProjectsLoading, setShowCreateModal } =
    useProjectsStore();

  const [importingProject, setImportingProject] = useState(false);
  const [conflictProject, setConflictProject] = useState<string | null>(null);
  const [conflictFile, setConflictFile] = useState<File | null>(null);
  const [importDiagnostics, setImportDiagnostics] = useState<ImportFailureDiagnostics | null>(null);
  const [showOpenClaw, setShowOpenClaw] = useState(false);
  const importInputRef = useRef<HTMLInputElement>(null);
  const isConfigComplete = useConfigStatusStore((s) => s.isComplete);

  const fetchProjects = useCallback(async () => {
    setProjectsLoading(true);
    try {
      const res = await API.listProjects();
      setProjects(res.projects);
    } finally {
      setProjectsLoading(false);
    }
  }, [setProjects, setProjectsLoading]);

  useEffect(() => {
    void fetchProjects();
  }, [fetchProjects]);

  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    await doImport(file);
    e.target.value = "";
  };

  const doImport = async (file: File, policy: ImportConflictPolicy = "prompt") => {
    setImportingProject(true);
    try {
      const result = await API.importProject(file, policy);
      setConflictProject(null);
      setConflictFile(null);
      setImportDiagnostics(null);
      await fetchProjects();

      const autoFixedCount = result.diagnostics.auto_fixed.length;
      const warningCount = result.diagnostics.warnings.length;
      if (warningCount > 0 || autoFixedCount > 0) {
        useAppStore.getState().pushToast(
          autoFixedCount > 0
            ? t("dashboard:import_auto_fixed", { title: result.project.title || result.project_name, count: autoFixedCount })
            : t("dashboard:import_success", { title: result.project.title || result.project_name }),
          "success"
        );
      }
      navigate(`/app/projects/${result.project_name}`);
    } catch (err) {
      const error = err as Error & {
        status?: number;
        conflict_project_name?: string;
        diagnostics?: ImportFailureDiagnostics;
      };

      if (error.status === 409 && error.conflict_project_name && policy === "prompt") {
        setConflictFile(file);
        setConflictProject(error.conflict_project_name);
        return;
      }

      if (error.diagnostics) {
        setImportDiagnostics(error.diagnostics);
      } else {
        alert(`${t("dashboard:import_failed")}: ${error.message}`);
      }
    } finally {
      setImportingProject(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      {/* Header */}
      <header className="border-b border-gray-800 bg-gray-900/50 px-6 py-4 backdrop-blur-sm">
        <div className="mx-auto flex max-w-6xl items-center justify-between">
          <h1 className="flex items-center text-xl font-bold">
            <img src="/android-chrome-192x192.png" alt="ArcReel" className="mr-2 h-6 w-6" />
            <span className="text-indigo-400">
              ArcReel
            </span>
            <span className="ml-1 text-gray-400 font-normal text-base">{t("dashboard:projects")}</span>
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
              {importingProject ? t("dashboard:importing") : t("dashboard:import_zip")}
            </button>
            <button
              type="button"
              onClick={() => setShowCreateModal(true)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 transition-colors cursor-pointer"
            >
              <Plus className="h-4 w-4" />
              {t("dashboard:create_project")}
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
                title={t("settings")}
                aria-label={t("settings")}
              >
                <Settings className="h-4 w-4" />
                {!isConfigComplete && (
                  <span className="absolute right-0.5 top-0.5 h-2 w-2 rounded-full bg-rose-500" aria-label={t("config_incomplete")} />
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
            <span className="ml-2 text-gray-400">{t("dashboard:loading_projects")}</span>
          </div>
        ) : projects.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-gray-500">
            <FolderOpen className="h-16 w-16 mb-4" />
            <p className="text-lg">{t("dashboard:no_projects")}</p>
            <p className="text-sm mt-1">{t("dashboard:start_creating_hint")}</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {projects.map((project) => (
              <ProjectCard key={project.name} project={project} />
            ))}
          </div>
        )}
      </main>

      {/* Overwrite / Rename Conflict Dialog */}
      {conflictProject && conflictFile && (
        <ConflictDialog
          projectName={conflictProject}
          importing={importingProject}
          onConfirm={(policy) => doImport(conflictFile, policy)}
          onCancel={() => {
            setConflictProject(null);
            setConflictFile(null);
          }}
        />
      )}

      {/* Import Diagnostics */}
      {importDiagnostics && (
        <ArchiveDiagnosticsDialog
          title={t("dashboard:export_diagnostics")}
          description={t("dashboard:import_success_with_diagnostics")}
          sections={[
            { key: "blocking", title: t("dashboard:blocking_issues"), tone: "border-red-400/25 bg-red-500/10 text-red-100", items: importDiagnostics.blocking },
            { key: "auto_fixed", title: t("dashboard:auto_fixed_issues"), tone: "border-indigo-400/25 bg-indigo-500/10 text-indigo-100", items: importDiagnostics.auto_fixable },
            { key: "warnings", title: t("common:error"), tone: "border-amber-400/25 bg-amber-500/10 text-amber-100", items: importDiagnostics.warnings },
          ]}
          onClose={() => setImportDiagnostics(null)}
        />
      )}
      {showOpenClaw && <OpenClawModal onClose={() => setShowOpenClaw(false)} />}
    </div>
  );
}

function ConflictDialog({
  projectName,
  importing,
  onConfirm,
  onCancel,
}: {
  projectName: string;
  importing: boolean;
  onConfirm: (policy: "overwrite" | "rename") => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation(["common", "dashboard"]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4 backdrop-blur-sm">
      <div className="w-full max-w-lg overflow-hidden rounded-2xl border border-gray-800 bg-gray-900 p-6 shadow-2xl">
        <div className="flex items-start gap-4">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-amber-500/10 text-amber-500">
            <AlertTriangle className="h-6 w-6" />
          </div>
          <div className="space-y-2">
            <h2 className="text-lg font-semibold text-gray-100">{t("dashboard:duplicate_project_id")}</h2>
            <p className="text-sm leading-6 text-gray-400">
              {t("dashboard:id_intended_hint")}
              <span className="mx-1 rounded bg-gray-800 px-1.5 py-0.5 font-mono text-gray-200">
                {projectName}
              </span>
              {t("dashboard:already_exists_conflict_hint")}
            </p>
          </div>
        </div>

        <div className="mt-5 grid gap-3">
          <button
            type="button"
            onClick={() => onConfirm("overwrite")}
            disabled={importing}
            aria-label={t("dashboard:overwrite_existing")}
            className="flex w-full items-center justify-between rounded-xl border border-red-400/25 bg-red-500/10 px-4 py-3 text-left text-sm text-red-100 transition-colors hover:border-red-300/40 hover:bg-red-500/15 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <span>
              <span className="block font-medium">{t("dashboard:overwrite_existing")}</span>
              <span className="mt-1 block text-xs text-red-200/80">
                {t("dashboard:overwrite_hint")}
              </span>
            </span>
            {importing && <Loader2 className="h-4 w-4 animate-spin" />}
          </button>

          <button
            type="button"
            onClick={() => onConfirm("rename")}
            disabled={importing}
            aria-label={t("dashboard:auto_rename_import")}
            className="flex w-full items-center justify-between rounded-xl border border-indigo-400/25 bg-indigo-500/10 px-4 py-3 text-left text-sm text-indigo-100 transition-colors hover:border-indigo-300/40 hover:bg-indigo-500/15 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <span>
              <span className="block font-medium">{t("dashboard:auto_rename_import")}</span>
              <span className="mt-1 block text-xs text-indigo-200/80">
                {t("dashboard:rename_hint")}
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
            {t("cancel")}
          </button>
        </div>
      </div>
    </div>
  );
}
