import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "wouter";
import { ChevronsUpDown, LayoutGrid, Plus, SlidersHorizontal } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { ROUTE_APP_PROJECTS, projectSettingsPath } from "@/app-routes";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useDemoWorkbench } from "@/onboarding/use-demo-workbench";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectSummary } from "@/types";
import { errMsg } from "@/utils/async";
import { getProjectDisplayName } from "@/utils/project-display";

const CONTENT_MODE_LABEL_KEY = {
  narration: "dashboard:narration_visuals",
  drama: "dashboard:drama_animation",
  ad: "dashboard:ad_short_video",
} as const;

type ProjectList = { status: "loading" } | { status: "ready"; projects: ProjectSummary[] } | { status: "error"; message: string };

/** 打开切换器时拉取一次全部项目；先用大厅已经拉到的列表顶上，新列表到达后替换。 */
function useProjectList(open: boolean): ProjectList {
  const cached = useProjectsStore((s) => s.projects);
  const [list, setList] = useState<ProjectList>({ status: "loading" });

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    API.listProjects({ signal: controller.signal }).then(
      (res) => {
        if (!controller.signal.aborted) setList({ status: "ready", projects: res.projects });
      },
      (err: unknown) => {
        if (!controller.signal.aborted) setList({ status: "error", message: errMsg(err) });
      },
    );
    return () => controller.abort();
  }, [open]);

  if (list.status === "loading" && cached.length > 0) return { status: "ready", projects: cached };
  return list;
}

/**
 * 顶栏左上的项目切换器：触发按钮显示当前项目的名称、创作类型与画幅；弹层里可以搜索全部项目，
 * 选中即切换，底部是项目设置、新建项目与全部项目。
 */
export function ProjectMenu() {
  const { t } = useTranslation(["dashboard", "common"]);
  const [, setLocation] = useLocation();
  const { currentProjectData, currentProjectName } = useProjectsStore();
  const setShowCreateModal = useProjectsStore((s) => s.setShowCreateModal);
  // 演示项目没有项目级设置
  const demoMode = useDemoWorkbench();
  const [open, setOpen] = useState(false);
  const list = useProjectList(open);
  const searchRef = useRef<HTMLInputElement>(null);

  const fallbackLabel = currentProjectName ? t("dashboard:untitled_project") : t("common:no_project_selected");
  const projectTitle = getProjectDisplayName(currentProjectData?.title, fallbackLabel);
  const contentMode = currentProjectData?.content_mode;
  const aspectRatio =
    typeof currentProjectData?.aspect_ratio === "string"
      ? currentProjectData.aspect_ratio
      : currentProjectData?.aspect_ratio?.storyboard;
  const modeLabel = contentMode ? t(CONTENT_MODE_LABEL_KEY[contentMode]) : null;
  const modeLine = [modeLabel, aspectRatio].filter(Boolean).join(" · ");

  const go = (path: string) => {
    setOpen(false);
    setLocation(path);
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button
            variant="ghost"
            className="h-9 min-w-0 shrink justify-start text-left"
            aria-label={t("dashboard:project_switcher_label", { name: projectTitle })}
          />
        }
      >
        <span className="flex min-w-0 flex-col">
          <TruncatedText text={projectTitle} focusable={false} className="text-sm font-medium" />
          {modeLine && <span className="truncate text-xs font-normal text-muted-foreground">{modeLine}</span>}
        </span>
        <ChevronsUpDown aria-hidden data-icon="inline-end" className="text-muted-foreground" />
      </PopoverTrigger>
      <PopoverContent align="start" initialFocus={searchRef} className="w-80">
        <Command loop className="-m-1.5">
          <CommandInput
            ref={searchRef}
            placeholder={t("dashboard:project_switcher_search")}
            aria-label={t("dashboard:project_switcher_search")}
          />
          {list.status === "ready" ? (
            <CommandList label={t("dashboard:project_switcher_projects")}>
              <CommandEmpty>{t("dashboard:project_switcher_no_match")}</CommandEmpty>
              <CommandGroup heading={t("dashboard:project_switcher_projects")}>
                {list.projects.map((project) => {
                  const title = getProjectDisplayName(project.title, t("dashboard:untitled_project"));
                  return (
                    <CommandItem
                      key={project.name}
                      value={project.name}
                      keywords={[title]}
                      data-checked={project.name === currentProjectName}
                      onSelect={() => go(`~${ROUTE_APP_PROJECTS}/${encodeURIComponent(project.name)}`)}
                    >
                      <span className="min-w-0 truncate">{title}</span>
                      {project.name === currentProjectName && (
                        <span className="sr-only">{t("dashboard:project_switcher_current")}</span>
                      )}
                    </CommandItem>
                  );
                })}
              </CommandGroup>
            </CommandList>
          ) : (
            <p role="status" className="px-2 py-6 text-center text-sm text-muted-foreground">
              {list.status === "loading"
                ? t("common:loading")
                : t("dashboard:project_switcher_load_failed", { message: list.message })}
            </p>
          )}
        </Command>
        {/* 底部入口不随搜索词过滤，放在候选列表之外，用 Tab 到达 */}
        <div className="-mx-1 flex flex-col border-t border-border pt-1.5">
          {currentProjectName && !demoMode && (
            <Link
              href={`~${projectSettingsPath(currentProjectName)}`}
              onClick={() => setOpen(false)}
              className={buttonVariants({ variant: "ghost", className: "justify-start" })}
            >
              <SlidersHorizontal aria-hidden data-icon="inline-start" />
              {t("dashboard:project_switcher_settings")}
            </Link>
          )}
          <Button
            variant="ghost"
            className="justify-start"
            onClick={() => {
              setShowCreateModal(true);
              go(`~${ROUTE_APP_PROJECTS}`);
            }}
          >
            <Plus aria-hidden data-icon="inline-start" />
            {t("dashboard:project_switcher_new")}
          </Button>
          <Link
            href={`~${ROUTE_APP_PROJECTS}`}
            onClick={() => setOpen(false)}
            className={buttonVariants({ variant: "ghost", className: "justify-start" })}
          >
            <LayoutGrid aria-hidden data-icon="inline-start" />
            {t("dashboard:project_switcher_all")}
          </Link>
        </div>
      </PopoverContent>
    </Popover>
  );
}
