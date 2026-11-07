import { useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "wouter";
import { useTranslation } from "react-i18next";
import {
  BookOpen,
  Clapperboard,
  FilePlus,
  FolderTree,
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
import { WORKSPACE_ROUTE_EPISODES, WORKSPACE_ROUTE_FILES } from "@/app-routes";
import { useDemoWorkbench } from "@/onboarding/use-demo-workbench";
import { normalizeRoute } from "@/utils/generation-mode";
import { CreateEpisodeDialog } from "@/components/canvas/episodes/CreateEpisodeDialog";
import { episodesViewPath } from "@/components/canvas/episodes/episodes-view-model";
import { useDeleteEpisode } from "@/components/canvas/episodes/useDeleteEpisode";
import { useMoveEpisode } from "@/components/canvas/episodes/useMoveEpisode";
import { EpisodeCard } from "./EpisodeCard";
import { SidebarEpisodeList } from "./SidebarEpisodeList";

interface AssetSidebarProps {
  /** Collapse to 56px icon rails, showing navigation/episode positions, with names on hover/focus. */
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
 * Workspace sidebars: overview/episodes/character/scene/prop navigation above, ads adding products without episodes;
 * episode search/order/actions below. Shell resize handles set widths, collapsed as icon rails.
 */
export function AssetSidebar({ collapsed, onCollapsedChange }: AssetSidebarProps) {
  const { t } = useTranslation(["common", "dashboard"]);
  const { currentProjectName, currentProjectData } = useProjectsStore();
  const debouncedFetchCost = useCostStore((s) => s.debouncedFetch);
  const [location, setLocation] = useLocation();
  const [search, setSearch] = useState("");
  /** New-episode dialogs: undefined closed, null append, numbers insert after episodes. */
  const [createAfter, setCreateAfter] = useState<number | null | undefined>(undefined);

  const episodes = currentProjectData?.episodes ?? [];
  // Ads/short films are single episodes; hide episode titles/count/search/add, navigating sole videos directly.
  const isAd = currentProjectData?.content_mode === "ad";
  // Demos lack backend data, hiding episode-management/add entries while preserving other navigation/lists.
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
    // Deleting current episodes returns to Episodes views.
    if (episode === activeEp) setLocation(episodesViewPath());
  });

  const navItems: NavItem[] = [
    { key: "overview", path: "/", label: t("dashboard:workspace_nav_overview"), icon: LayoutDashboard },
    // Missing demo backends and single-episode ads bypass episode management; hide failing/meaningless entries instead of rendering them.
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
    // Product assets are ad/short-film only, v1 single-product design; hide elsewhere.
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
    // Demos lack server files to list.
    ...(demoMode
      ? []
      : [{ key: "files", path: `/${WORKSPACE_ROUTE_FILES}`, label: t("dashboard:workspace_nav_files"), icon: FolderTree }]),
  ];

  const isNavActive = (item: NavItem): boolean => {
    // Episode /episodes/:id highlights come from lists below; Episodes highlights only management views.
    if (item.path === "/" || item.key === "episodes") return location === item.path;
    return location === item.path || location.startsWith(item.path + "/");
  };

  // Broadcast positions follow full ledgers, unchanged by filters; searches use titles/positions, never IDs.
  const positioned = episodes.map((ep, index) => ({ ep, position: index + 1 }));
  // Ads hide search; ignore residual search state so sole-video entries remain visible.
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
          {/* Icon rails hide search fields; invisible filters must never make episodes disappear. */}
          {positioned.map(({ ep, position }) => (
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
                onMove={moveEpisode}
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

/** Icon-rail items show icons/episode positions, names through hover/focus hints and complete screen-reader labels. */
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
