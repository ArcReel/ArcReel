// PROTOTYPE — 工作区外壳原型（#2970）的顶栏部件：项目菜单的两种新形态、设置入口、Agent 常驻按钮。
// 文案直接写中文，原型不走 i18n。

import { useEffect, useState } from "react";
import { useLocation } from "wouter";
import {
  Bot,
  Check,
  ChevronDown,
  Download,
  FolderOpen,
  LayoutGrid,
  Package,
  Plus,
  Settings,
  SlidersHorizontal,
} from "lucide-react";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { getProjectDisplayName } from "@/utils/project-display";
import { rememberAssetLibraryReturnTo } from "@/components/pages/AssetLibraryPage";
import type { ProjectSummary } from "@/types";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useShellProto } from "./store";

const ICON_BTN =
  "relative grid size-8 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-ring";

function useProjectTitle() {
  const { currentProjectData, currentProjectName } = useProjectsStore();
  const title = getProjectDisplayName(currentProjectData?.title, currentProjectName ?? "未选择项目");
  return { title, initial: (title || "?").slice(0, 1).toUpperCase(), currentProjectName };
}

function ProjectAvatar({ initial }: { initial: string }) {
  return (
    <span className="grid size-6 shrink-0 place-items-center rounded-md bg-primary text-[12px] font-semibold text-primary-foreground">
      {initial}
    </span>
  );
}

/** 项目切换器：搜索 + 全部项目列表，选中即切换；底部是新建与全部项目。 */
export function ProtoProjectSwitcher() {
  const [, setLocation] = useLocation();
  const { title, initial, currentProjectName } = useProjectTitle();
  const { axes } = useShellProto();
  const [open, setOpen] = useState(false);
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);

  useEffect(() => {
    if (!open || projects) return;
    void API.listProjects().then((r) => setProjects(r.projects));
  }, [open, projects]);

  const go = (path: string) => {
    setOpen(false);
    setLocation(path);
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        className="flex min-w-0 items-center gap-2 rounded-md px-1.5 py-1 text-[13px] font-medium text-foreground hover:bg-accent focus-ring"
        aria-label="切换项目"
      >
        <ProjectAvatar initial={initial} />
        <span className="truncate">{title}</span>
        <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 p-0">
        <Command>
          <CommandInput placeholder="搜索项目" />
          <CommandList className="max-h-80">
            <CommandEmpty>{projects ? "没有匹配的项目" : "加载中…"}</CommandEmpty>
            {projects && (
              <CommandGroup heading="项目">
                {projects.map((p) => (
                  <CommandItem
                    key={p.name}
                    value={`${p.title} ${p.name}`}
                    onSelect={() => go(`~/app/projects/${encodeURIComponent(p.name)}`)}
                  >
                    <span className="truncate">{getProjectDisplayName(p.title, p.name)}</span>
                    {p.name === currentProjectName && <Check className="ml-auto size-3.5" />}
                  </CommandItem>
                ))}
              </CommandGroup>
            )}
            <CommandSeparator />
            <CommandGroup>
              {axes.settings === "menu" && currentProjectName && (
                <CommandItem onSelect={() => go(`~/app/projects/${encodeURIComponent(currentProjectName)}/settings`)}>
                  <SlidersHorizontal />
                  项目设置
                </CommandItem>
              )}
              <CommandItem onSelect={() => go("~/app/projects")}>
                <Plus />
                新建项目
              </CommandItem>
              <CommandItem onSelect={() => go("~/app/projects")}>
                <LayoutGrid />
                全部项目
              </CommandItem>
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

/** 「项目操作」：只管当前项目能做什么；切换项目靠左侧「返回项目大厅」。 */
export function ProtoProjectActions({ onExport }: { onExport: () => void }) {
  const [, setLocation] = useLocation();
  const { title, initial, currentProjectName } = useProjectTitle();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="flex min-w-0 items-center gap-2 rounded-md px-1.5 py-1 text-[13px] font-medium text-foreground hover:bg-accent focus-ring"
        aria-label="项目操作"
      >
        <ProjectAvatar initial={initial} />
        <span className="truncate">{title}</span>
        <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-56">
        <DropdownMenuGroup>
          <DropdownMenuItem
            disabled={!currentProjectName}
            onClick={() => currentProjectName && setLocation(`~/app/projects/${encodeURIComponent(currentProjectName)}/settings`)}
          >
            <SlidersHorizontal />
            项目设置
          </DropdownMenuItem>
          <DropdownMenuItem onClick={onExport}>
            <Download />
            导出项目…
          </DropdownMenuItem>
          <DropdownMenuItem
            onClick={() => {
              rememberAssetLibraryReturnTo(window.location.pathname);
              setLocation("~/app/assets");
            }}
          >
            <Package />
            资产库
          </DropdownMenuItem>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuItem onClick={() => setLocation("~/app/projects")}>
            <FolderOpen />
            全部项目
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => setLocation("~/app/projects")}>
            <Plus />
            新建项目
          </DropdownMenuItem>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function IconLink({ label, onClick, children }: { label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger className={ICON_BTN} onClick={onClick} aria-label={label}>
        {children}
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

/** 设置入口的 split 与 menu 两种形态；single 仍由 GlobalHeader 原有按钮负责。 */
export function ProtoSettingsEntries({ configIncomplete }: { configIncomplete: boolean }) {
  const [, setLocation] = useLocation();
  const { axes } = useShellProto();
  const { currentProjectName } = useProjectsStore();
  const dot = configIncomplete ? (
    <span className="absolute right-1 top-1 size-2 rounded-full bg-destructive" aria-label="配置未完成" />
  ) : null;
  return (
    <>
      {axes.settings === "split" && currentProjectName && (
        <IconLink label="项目设置" onClick={() => setLocation(`~/app/projects/${encodeURIComponent(currentProjectName)}/settings`)}>
          <SlidersHorizontal className="size-4" />
        </IconLink>
      )}
      <IconLink label="全局设置" onClick={() => setLocation("~/app/settings")}>
        <Settings className="size-4" />
        {dot}
      </IconLink>
    </>
  );
}

/** Agent 运行状态：运行中 = 呼吸点，等待回答 = 琥珀点。收起态的入口都用它。 */
export function useAgentAttention(): "running" | "question" | null {
  const running = useAssistantStore((s) => s.sessionStatus === "running");
  const question = useAssistantStore((s) => Boolean(s.pendingQuestion));
  if (question) return "question";
  if (running) return "running";
  return null;
}

export function AttentionDot({ kind, className = "" }: { kind: "running" | "question" | null; className?: string }) {
  if (!kind) return null;
  return (
    <span
      className={`size-2 rounded-full ${kind === "question" ? "bg-[var(--color-warn)]" : "animate-pulse bg-primary"} ${className}`}
      aria-label={kind === "question" ? "Agent 在等你回答" : "Agent 运行中"}
    />
  );
}

/** 顶栏右端常驻的 Agent 开关：开着时是按下态，收起后靠它和状态点找回。 */
export function ProtoAgentToggle() {
  const open = useAppStore((s) => s.assistantPanelOpen);
  const toggle = useAppStore((s) => s.toggleAssistantPanel);
  const attention = useAgentAttention();
  return (
    <button
      type="button"
      onClick={toggle}
      aria-pressed={open}
      className={`ml-1 inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-[13px] transition-colors focus-ring ${
        open ? "bg-primary/15 text-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground"
      }`}
      title={open ? "收起 Agent 面板" : "打开 Agent 面板"}
    >
      <Bot className="size-4" />
      <span>Agent</span>
      <AttentionDot kind={open ? null : attention} />
    </button>
  );
}
