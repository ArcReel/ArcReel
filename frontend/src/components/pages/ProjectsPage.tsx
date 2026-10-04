import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Clapperboard, Loader2, Plus, Upload } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { PageShell } from "@/components/shared/page-shell/PageShell";
import { Button } from "@/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { OnboardingDemoCard } from "@/onboarding/OnboardingDemoCard";
import { useAppStore } from "@/stores/app-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { useProjectsStore } from "@/stores/projects-store";
import { errMsg } from "@/utils/async";
import type { ProjectSummary } from "@/types";
import { CreateProjectModal } from "./CreateProjectModal";
import { LobbyGreeting } from "./lobby/LobbyGreeting";
import { LobbyHeader } from "./lobby/LobbyHeader";
import { LobbyToolbar } from "./lobby/LobbyToolbar";
import { ProjectCard, type ProjectCardActions } from "./lobby/ProjectCard";
import { DeleteProjectDialog, RenameProjectDialog } from "./lobby/ProjectDialogs";
import { LOBBY_FILTERS, asProjectStatus, matchesFilter, type LobbyFilter } from "./lobby/lobby-projects";
import { useProjectExport } from "@/components/layout/useProjectExport";
import { useProjectImport } from "./lobby/useProjectImport";

/**
 * 项目列表的加载。首次加载显示加载状态；改名、删除、导入之后静默重新加载，列表不闪回加载状态。
 * 新一轮加载作废上一轮在途的请求。
 */
function useLobbyProjects() {
  const { t } = useTranslation("dashboard");
  const { projects, projectsLoading, setProjects, setProjectsLoading } = useProjectsStore();
  const controllerRef = useRef<AbortController | null>(null);

  const load = useCallback(
    async (quiet: boolean) => {
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;
      if (!quiet) setProjectsLoading(true);
      try {
        const res = await API.listProjects({ signal: controller.signal });
        if (controller.signal.aborted) return;
        setProjects(res.projects);
      } catch (err) {
        if (controller.signal.aborted) return;
        useAppStore.getState().pushToast(t("lobby_load_failed", { message: errMsg(err) }), "error");
      } finally {
        if (!controller.signal.aborted) setProjectsLoading(false);
      }
    },
    [setProjects, setProjectsLoading, t],
  );

  useEffect(() => {
    void load(false);
    return () => controllerRef.current?.abort();
  }, [load]);

  const reload = useCallback(() => void load(true), [load]);
  return { projects, loading: projectsLoading, reload };
}

export function ProjectsPage() {
  const { t } = useTranslation("dashboard");
  const { projects, loading, reload } = useLobbyProjects();
  const showCreateModal = useProjectsStore((s) => s.showCreateModal);
  const setShowCreateModal = useProjectsStore((s) => s.setShowCreateModal);
  const tourActive = useOnboardingStore((s) => s.active);
  const [filter, setFilter] = useState<LobbyFilter>("all");
  const [query, setQuery] = useState("");
  const [renaming, setRenaming] = useState<ProjectSummary | null>(null);
  const [deleting, setDeleting] = useState<ProjectSummary | null>(null);
  const projectImport = useProjectImport({ onImported: reload });
  const projectExport = useProjectExport();

  const counts = useMemo(() => {
    const out: Record<LobbyFilter, number> = { all: 0, in_progress: 0, completed: 0, repair: 0 };
    for (const project of projects) {
      const status = asProjectStatus(project.status);
      for (const key of LOBBY_FILTERS) if (matchesFilter(status, key)) out[key] += 1;
    }
    return out;
  }, [projects]);

  const episodeTotals = useMemo(() => {
    let completed = 0;
    let inProduction = 0;
    for (const project of projects) {
      const summary = asProjectStatus(project.status)?.episodes_summary;
      completed += summary?.completed ?? 0;
      inProduction += summary?.in_production ?? 0;
    }
    return { completed, inProduction };
  }, [projects]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return projects.filter(
      (project) =>
        matchesFilter(asProjectStatus(project.status), filter) &&
        (!q || `${project.title} ${project.name}`.toLowerCase().includes(q)),
    );
  }, [projects, filter, query]);

  const openCreate = () => setShowCreateModal(true);
  const actions: ProjectCardActions = {
    onRename: setRenaming,
    onExport: (project) => projectExport.open(project.name),
    onDelete: setDeleting,
  };

  let content;
  if (loading) {
    content = (
      <div role="status" className="flex items-center justify-center gap-2 py-20 text-sm text-muted-foreground">
        <Loader2 aria-hidden className="size-5 animate-spin text-primary" />
        {t("loading_projects")}
      </div>
    );
  } else if (projects.length === 0) {
    content = (
      <Empty>
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <Clapperboard aria-hidden />
          </EmptyMedia>
          <EmptyTitle>{t("lobby_empty_title")}</EmptyTitle>
          <EmptyDescription>{t("lobby_empty_desc")}</EmptyDescription>
        </EmptyHeader>
        <EmptyContent className="flex-row justify-center">
          <Button onClick={openCreate}>
            <Plus aria-hidden data-icon="inline-start" />
            {t("lobby_new_project")}
          </Button>
          <Button variant="outline" disabled={projectImport.importing} onClick={projectImport.openPicker}>
            {projectImport.importing ? (
              <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
            ) : (
              <Upload aria-hidden data-icon="inline-start" />
            )}
            {t("import_zip")}
          </Button>
        </EmptyContent>
      </Empty>
    );
  } else {
    content = (
      <>
        <LobbyToolbar filter={filter} onFilterChange={setFilter} counts={counts} />
        {shown.length === 0 ? (
          <Empty>
            <EmptyHeader>
              <EmptyTitle>{t("lobby_no_filter_match")}</EmptyTitle>
              <EmptyDescription>{t("lobby_no_filter_match_hint")}</EmptyDescription>
            </EmptyHeader>
            <EmptyContent>
              <Button
                variant="outline"
                onClick={() => {
                  setFilter("all");
                  setQuery("");
                }}
              >
                {t("lobby_clear_filters")}
              </Button>
            </EmptyContent>
          </Empty>
        ) : (
          <ul aria-label={t("lobby_projects_label")} className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-4 pt-5">
            {shown.map((project) => (
              <li key={project.name} className="flex min-w-0 flex-col">
                <ProjectCard project={project} actions={actions} />
              </li>
            ))}
          </ul>
        )}
      </>
    );
  }

  return (
    <PageShell
      tier="full"
      header={
        <LobbyHeader
          query={query}
          onQueryChange={setQuery}
          onCreate={openCreate}
          onImport={projectImport.openPicker}
          importing={projectImport.importing}
        />
      }
    >
      {/* 问候区的状态句取决于项目列表，加载完成后才播放打字机，避免先打出空状态的句子再重来。 */}
      {!loading && (
        <LobbyGreeting
          projectCount={projects.length}
          inProgressCount={counts.in_progress}
          episodesCompleted={episodeTotals.completed}
          episodesInProduction={episodeTotals.inProduction}
        />
      )}
      {/* 引导运行期间才挂，退出即卸载。放在加载与空状态的分支之外：首次使用时项目列表通常是空的，
          而演示卡正是那一刻最需要讲的东西。 */}
      {tourActive && <OnboardingDemoCard />}
      {content}

      {projectImport.element}
      {projectExport.element}
      <RenameProjectDialog project={renaming} onClose={() => setRenaming(null)} onDone={reload} />
      <DeleteProjectDialog project={deleting} onClose={() => setDeleting(null)} onDone={reload} />
      {showCreateModal && <CreateProjectModal />}
    </PageShell>
  );
}
