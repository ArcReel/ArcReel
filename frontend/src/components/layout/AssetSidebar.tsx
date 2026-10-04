import { useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "wouter";
import { useTranslation } from "react-i18next";
import {
  BookOpen,
  Clapperboard,
  FilePlus,
  Landmark,
  LayoutDashboard,
  Package,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
  ShoppingBag,
  Upload,
  Users,
  type LucideIcon,
} from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { Separator } from "@/components/ui/separator";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useProjectsStore } from "@/stores/projects-store";
import { useCostStore } from "@/stores/cost-store";
import { WORKSPACE_ROUTE_EPISODES } from "@/app-routes";
import { useDemoWorkbench } from "@/onboarding/use-demo-workbench";
import { normalizeRoute } from "@/utils/generation-mode";
import { CreateEpisodeDialog } from "@/components/canvas/episodes/CreateEpisodeDialog";
import { episodesViewPath } from "@/components/canvas/episodes/episodes-view-model";
import { useDeleteEpisode } from "@/components/canvas/episodes/useDeleteEpisode";
import { useMoveEpisode } from "@/components/canvas/episodes/useMoveEpisode";
import { EpisodeCard } from "./EpisodeCard";
import { SidebarEpisodeList } from "./SidebarEpisodeList";

interface AssetSidebarProps {
  /** 收为 56px 图标栏：只显示导航图标与集序号，名称在悬停或聚焦时显示。 */
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
}

interface NavItem {
  key: string;
  path: string;
  label: string;
  icon: LucideIcon;
  count?: number;
}

/**
 * 工作区侧栏：上方是工作区导航（项目概览、分集、角色、场景、道具，广告项目另有商品、没有分集），
 * 下方是分集列表（搜索、排序与每集的操作）。宽度由外壳的调宽手柄决定；折叠时收为图标栏。
 */
export function AssetSidebar({ collapsed, onCollapsedChange }: AssetSidebarProps) {
  const { t } = useTranslation(["common", "dashboard"]);
  const { currentProjectName, currentProjectData } = useProjectsStore();
  const debouncedFetchCost = useCostStore((s) => s.debouncedFetch);
  const [location, setLocation] = useLocation();
  const [search, setSearch] = useState("");
  /** 新建一集对话框：undefined 为关闭，null 放在末尾，数字为插在这一集之后。 */
  const [createAfter, setCreateAfter] = useState<number | null | undefined>(undefined);

  const episodes = currentProjectData?.episodes ?? [];
  // 广告/短片项目恒单集：隐藏「集」语义（标题/计数/搜索/添加），直达唯一视频
  const isAd = currentProjectData?.content_mode === "ad";
  // 演示项目没有服务端侧数据，「分集」入口与添加菜单隐藏（导航其余项与分集列表照常渲染）
  const demoMode = useDemoWorkbench();
  const route = normalizeRoute(currentProjectData?.generation_mode);

  useEffect(() => {
    if (currentProjectName) debouncedFetchCost(currentProjectName);
  }, [currentProjectName, debouncedFetchCost]);

  // Derive active episode from `/episodes/:id`
  const activeEp = useMemo(() => {
    const m = location.match(/^\/episodes\/(\d+)/);
    return m ? parseInt(m[1], 10) : null;
  }, [location]);

  const moveEpisode = useMoveEpisode(currentProjectName);
  const deletion = useDeleteEpisode(currentProjectName ?? "", (episode) => {
    // 删的是正在看的那一集时回到「分集」视图
    if (episode === activeEp) setLocation(episodesViewPath());
  });

  const navItems: NavItem[] = [
    { key: "overview", path: "/", label: t("dashboard:workspace_nav_overview"), icon: LayoutDashboard },
    // 演示项目后端不存在，广告/短片恒单集、不经分集：隐藏入口而非渲染必然报错或无意义的页面
    ...(demoMode || isAd
      ? []
      : [
          {
            key: "episodes",
            path: `/${WORKSPACE_ROUTE_EPISODES}`,
            label: t("dashboard:workspace_nav_episodes"),
            icon: BookOpen,
            count: episodes.length,
          },
        ]),
    {
      key: "characters",
      path: "/characters",
      label: t("dashboard:workspace_nav_characters"),
      icon: Users,
      count: Object.keys(currentProjectData?.characters ?? {}).length,
    },
    {
      key: "scenes",
      path: "/scenes",
      label: t("dashboard:workspace_nav_scenes"),
      icon: Landmark,
      count: Object.keys(currentProjectData?.scenes ?? {}).length,
    },
    {
      key: "props",
      path: "/props",
      label: t("dashboard:workspace_nav_props"),
      icon: Package,
      count: Object.keys(currentProjectData?.props ?? {}).length,
    },
    // 商品资产仅广告/短片项目使用（v1 单商品设定），其余模式隐藏入口
    ...(isAd
      ? [
          {
            key: "products",
            path: "/products",
            label: t("dashboard:workspace_nav_products"),
            icon: ShoppingBag,
            count: Object.keys(currentProjectData?.products ?? {}).length,
          },
        ]
      : []),
  ];

  const isNavActive = (item: NavItem): boolean => {
    // 集页 /episodes/:id 由下方分集列表高亮，「分集」只在分集视图本身高亮
    if (item.path === "/" || item.key === "episodes") return location === item.path;
    return location === item.path || location.startsWith(item.path + "/");
  };

  // 播出位置按完整账本的排列算，过滤不改变它；搜索按标题与播出位置匹配，集 ID 不参与。
  const positioned = episodes.map((ep, index) => ({ ep, position: index + 1 }));
  // ad 隐藏搜索框，残留的 search state 不参与过滤，避免唯一视频入口被吞
  const filteredEps = isAd
    ? positioned
    : positioned.filter(
        ({ ep, position }) => !search || ep.title.includes(search) || String(position).includes(search),
      );

  const sectionTitle = isAd ? t("dashboard:ad_video_section_title") : t("dashboard:episodes_section_title");
  const addEpisodeMenu =
    isAd || demoMode ? null : (
      <AddEpisodeMenu
        onCreate={() => setCreateAfter(null)}
        onUpload={() => setLocation(episodesViewPath({ upload: "episode" }))}
      />
    );

  return (
    <div className="@container/sidebar relative flex h-full min-w-0 flex-col">
      <nav aria-label={t("dashboard:workspace_nav_label")} className={cn("flex shrink-0 flex-col gap-0.5 p-2", collapsed && "items-center")}>
        {navItems.map((item) =>
          collapsed ? (
            <RailLink key={item.key} href={item.path} label={item.label} icon={item.icon} active={isNavActive(item)} />
          ) : (
            <NavLink key={item.key} item={item} active={isNavActive(item)} />
          ),
        )}
      </nav>

      <Separator />

      {collapsed ? (
        <div className="relative flex min-h-0 flex-1 flex-col items-center gap-1 overflow-y-auto py-2">
          {addEpisodeMenu}
          {filteredEps.map(({ ep, position }) => (
            <RailLink
              key={ep.episode}
              href={`/episodes/${ep.episode}`}
              active={ep.episode === activeEp}
              label={
                isAd
                  ? sectionTitle
                  : t("dashboard:episode_collapsed_button_label", {
                      position,
                      title: ep.title || t("common:episode_position_name", { position }),
                    })
              }
              icon={isAd ? Clapperboard : undefined}
              text={isAd ? undefined : String(position)}
            />
          ))}
        </div>
      ) : (
        <section aria-label={sectionTitle} className="flex min-h-0 flex-1 flex-col">
          <div className="flex shrink-0 items-center gap-2 px-4 pb-1 pt-3">
            <h2 className="text-xs font-medium text-muted-foreground">{sectionTitle}</h2>
            {!isAd && <span className="text-xs tabular-nums text-muted-foreground">{episodes.length}</span>}
            <span className="flex-1" />
            {addEpisodeMenu}
          </div>

          {!isAd && (
            <div className="shrink-0 px-2 pb-2">
              <InputGroup>
                <InputGroupAddon>
                  <Search aria-hidden />
                </InputGroupAddon>
                <InputGroupInput
                  type="search"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={t("dashboard:episode_search_placeholder")}
                  aria-label={t("dashboard:episode_search_placeholder")}
                />
              </InputGroup>
            </div>
          )}

          <div className="relative min-h-0 flex-1 overflow-y-auto px-2 pb-2">
            {filteredEps.length === 0 ? (
              <p className="px-2 py-6 text-center text-xs text-muted-foreground">
                {episodes.length === 0 ? t("dashboard:no_episodes_yet") : t("dashboard:no_episode_search_results")}
              </p>
            ) : isAd || demoMode ? (
              filteredEps.map(({ ep, position }) => (
                <EpisodeCard
                  key={ep.episode}
                  ep={ep}
                  position={position}
                  active={ep.episode === activeEp}
                  onClick={() => setLocation(`/episodes/${ep.episode}`)}
                  showEpisodeBadge={!isAd}
                  fallbackTitle={isAd ? currentProjectData?.title : undefined}
                  route={route}
                />
              ))
            ) : (
              <SidebarEpisodeList
                episodes={episodes}
                shown={filteredEps}
                wholeSourceFiles={currentProjectData?.whole_source_files ?? []}
                activeEp={activeEp}
                route={route}
                reorderable={!search}
                onOpen={(episode) => setLocation(`/episodes/${episode}`)}
                onCreateAfter={setCreateAfter}
                onMove={(episode, after) => void moveEpisode(episode, after)}
                onDelete={(episode) => void deletion.requestDelete(episode)}
              />
            )}
          </div>
        </section>
      )}

      <div className={cn("flex shrink-0 border-t border-border p-2", collapsed && "justify-center")}>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-expanded={!collapsed}
          aria-label={collapsed ? t("dashboard:sidebar_expand") : t("dashboard:sidebar_collapse")}
          onClick={() => onCollapsedChange(!collapsed)}
        >
          {collapsed ? <PanelLeftOpen aria-hidden /> : <PanelLeftClose aria-hidden />}
        </Button>
      </div>

      {createAfter !== undefined && currentProjectName ? (
        <CreateEpisodeDialog
          projectName={currentProjectName}
          initialAfter={createAfter}
          onClose={() => setCreateAfter(undefined)}
          onCreated={(episode) => {
            setCreateAfter(undefined);
            setLocation(`/episodes/${episode}`);
          }}
        />
      ) : null}
      {deletion.dialog}
    </div>
  );
}

function NavLink({ item, active }: { item: NavItem; active: boolean }) {
  const Icon = item.icon;
  return (
    <Link
      href={item.path}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex min-h-8 items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
        active ? "bg-primary/15 text-foreground" : "text-subtle-foreground hover:bg-muted/50 hover:text-foreground",
      )}
    >
      <Icon aria-hidden className={cn("size-4 shrink-0", active ? "text-primary" : "text-muted-foreground")} />
      <span className="min-w-0 flex-1 truncate">{item.label}</span>
      {item.count !== undefined && <span className="text-xs tabular-nums text-muted-foreground">{item.count}</span>}
    </Link>
  );
}

/** 图标栏里的一项：只显示图标或集序号，名称经悬停或聚焦的提示显示，读屏读完整名称。 */
function RailLink({
  href,
  label,
  icon: Icon,
  text,
  active,
}: {
  href: string;
  label: string;
  icon?: LucideIcon;
  text?: string;
  active: boolean;
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Link
            href={href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex size-10 shrink-0 items-center justify-center rounded-md text-xs font-medium tabular-nums transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
              active ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
            )}
          />
        }
      >
        {Icon ? <Icon aria-hidden className="size-4" /> : <span aria-hidden>{text}</span>}
        <span className="sr-only">{label}</span>
      </TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );
}

function AddEpisodeMenu({ onCreate, onUpload }: { onCreate: () => void; onUpload: () => void }) {
  const { t } = useTranslation("dashboard");
  return (
    <DropdownMenu>
      <DropdownMenuTrigger render={<Button variant="ghost" size="icon-xs" aria-label={t("add_episode")} />}>
        <Plus aria-hidden />
      </DropdownMenuTrigger>
      <DropdownMenuContent aria-label={t("add_episode")} className="w-52">
        <DropdownMenuItem onClick={onCreate}>
          <FilePlus aria-hidden />
          {t("episode_create_title")}
        </DropdownMenuItem>
        <DropdownMenuItem onClick={onUpload}>
          <Upload aria-hidden />
          {t("episode_menu_upload_sources")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
