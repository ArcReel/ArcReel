import { useEffect, useMemo, useState } from "react";
import { useLocation } from "wouter";
import { useTranslation } from "react-i18next";
import {
  ChevronLeft,
  ChevronRight,
  LayoutDashboard,
  BookOpen,
  Users,
  Landmark,
  Package,
  Plus,
  Search,
} from "lucide-react";
import { useProjectsStore } from "@/stores/projects-store";
import { useCostStore } from "@/stores/cost-store";
import { useAppStore } from "@/stores/app-store";
import { API } from "@/api";
import { EpisodeCard } from "./EpisodeCard";

interface AssetSidebarProps {
  className?: string;
}

interface NavItem {
  key: string;
  path: string;
  label: string;
  icon: React.ComponentType<{ className?: string; style?: React.CSSProperties }>;
  meta?: number;
}

/**
 * 工作台侧栏 v3：
 * - 工作区导航（5 个胶囊按钮：项目概览 / 源文件 / 角色集 / 场景库 / 道具库）
 * - 分集列表（搜索 + 卡片列表，每张卡片含缩略+状态+进度+费用）
 * - 折叠态（64px）：仅图标 + Ex 字符
 */
export function AssetSidebar({ className }: AssetSidebarProps) {
  const { t } = useTranslation(["common", "dashboard"]);
  const { currentProjectName, currentProjectData } = useProjectsStore();
  const debouncedFetchCost = useCostStore((s) => s.debouncedFetch);
  const [location, setLocation] = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [search, setSearch] = useState("");

  const characterCount = Object.keys(currentProjectData?.characters ?? {}).length;
  const sceneCount = Object.keys(currentProjectData?.scenes ?? {}).length;
  const propCount = Object.keys(currentProjectData?.props ?? {}).length;
  const episodes = currentProjectData?.episodes ?? [];

  const sourceFilesVersion = useAppStore((s) => s.sourceFilesVersion);
  const [sourceCount, setSourceCount] = useState<number>(0);

  useEffect(() => {
    if (currentProjectName) debouncedFetchCost(currentProjectName);
  }, [currentProjectName, debouncedFetchCost]);

  useEffect(() => {
    if (!currentProjectName) return;
    let cancelled = false;
    API.listFiles(currentProjectName)
      .then((res) => {
        if (!cancelled) setSourceCount(res.files?.source?.length ?? 0);
      })
      .catch(() => {
        if (!cancelled) setSourceCount(0);
      });
    return () => {
      cancelled = true;
    };
  }, [currentProjectName, sourceFilesVersion]);

  // Derive active episode from `/episodes/:id`
  const activeEp = useMemo(() => {
    const m = location.match(/^\/episodes\/(\d+)/);
    return m ? parseInt(m[1], 10) : null;
  }, [location]);

  const navItems: NavItem[] = [
    { key: "overview", path: "/", label: t("dashboard:workspace_nav_overview"), icon: LayoutDashboard },
    {
      key: "source",
      path: "/source",
      label: t("dashboard:workspace_nav_source"),
      icon: BookOpen,
      meta: sourceCount,
    },
    {
      key: "characters",
      path: "/characters",
      label: t("dashboard:workspace_nav_characters"),
      icon: Users,
      meta: characterCount,
    },
    {
      key: "scenes",
      path: "/scenes",
      label: t("dashboard:workspace_nav_scenes"),
      icon: Landmark,
      meta: sceneCount,
    },
    {
      key: "props",
      path: "/props",
      label: t("dashboard:workspace_nav_props"),
      icon: Package,
      meta: propCount,
    },
  ];

  const isNavActive = (item: NavItem): boolean => {
    if (item.path === "/") return location === "/";
    return location === item.path || location.startsWith(item.path + "/");
  };

  const filteredEps = episodes.filter(
    (ep) => !search || ep.title.includes(search) || String(ep.episode).includes(search),
  );

  return (
    <aside
      className={`flex flex-col overflow-hidden ${className ?? ""}`}
      style={{
        width: collapsed ? 64 : 256,
        transition: "width .18s ease",
        borderRight: "1px solid var(--color-hairline)",
        background:
          "linear-gradient(180deg, oklch(0.195 0.011 265 / 0.6), oklch(0.175 0.010 265 / 0.5))",
        boxShadow: "inset -1px 0 0 oklch(1 0 0 / 0.015)",
      }}
    >
      {/* ---- Workspace nav ---- */}
      <div className="px-2.5 pb-1.5 pt-2.5">
        {navItems.map((item) => {
          const Icon = item.icon;
          const active = isNavActive(item);
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => setLocation(item.path)}
              title={collapsed ? item.label : ""}
              className="relative mb-px flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 transition-colors focus-ring"
              style={{
                background: active
                  ? "linear-gradient(90deg, var(--color-accent-soft), var(--color-accent-dim) 70%, transparent)"
                  : "transparent",
                color: active ? "var(--color-text)" : "var(--color-text-2)",
              }}
              onMouseEnter={(e) => {
                if (!active) e.currentTarget.style.background = "oklch(0.26 0.012 265 / 0.5)";
              }}
              onMouseLeave={(e) => {
                if (!active) e.currentTarget.style.background = "transparent";
              }}
            >
              {active && (
                <span
                  className="absolute -left-px top-[7px] bottom-[7px] w-0.5 rounded"
                  style={{
                    background: "var(--color-accent)",
                    boxShadow: "0 0 8px var(--color-accent-glow)",
                  }}
                />
              )}
              <span
                className="grid w-4 shrink-0 place-items-center"
                style={{ color: active ? "var(--color-accent-2)" : "var(--color-text-3)" }}
              >
                <Icon className="h-4 w-4" />
              </span>
              {!collapsed && (
                <>
                  <span
                    className="flex-1 text-left text-[13px]"
                    style={{
                      fontWeight: active ? 600 : 500,
                      letterSpacing: "-0.05px",
                    }}
                  >
                    {item.label}
                  </span>
                  {item.meta != null && (
                    <span
                      className="num rounded-[3px] px-1.5 py-px text-[10.5px]"
                      style={{
                        color: active ? "var(--color-text-3)" : "var(--color-text-4)",
                        background: active ? "oklch(0 0 0 / 0.2)" : "transparent",
                      }}
                    >
                      {item.meta}
                    </span>
                  )}
                </>
              )}
            </button>
          );
        })}
      </div>

      <div
        className="mx-3.5 my-1 h-px"
        style={{ background: "var(--color-hairline-soft)" }}
      />

      {/* ---- Episodes ---- */}
      {!collapsed ? (
        <>
          <div className="flex items-center gap-2 px-3.5 pb-1.5 pt-2.5">
            <span
              className="text-[10.5px] font-bold uppercase"
              style={{ color: "var(--color-text-4)", letterSpacing: "0.8px" }}
            >
              {t("dashboard:episodes_section_title")}
            </span>
            <span className="num text-[10px]" style={{ color: "var(--color-text-4)" }}>
              {episodes.length}
            </span>
            <span className="flex-1" />
            <button
              type="button"
              disabled
              aria-disabled="true"
              className="grid h-5 w-5 place-items-center rounded focus-ring disabled:cursor-not-allowed disabled:opacity-50"
              style={{
                background: "oklch(0.28 0.012 250 / 0.6)",
                color: "var(--color-text-3)",
              }}
              title={t("dashboard:add_episode_unavailable")}
              aria-label={t("dashboard:add_episode")}
            >
              <Plus className="h-3 w-3" />
            </button>
          </div>

          <div className="px-2.5 pb-2">
            <div
              className="flex items-center gap-1.5 rounded-md px-2 py-1.5"
              style={{
                background: "oklch(0.16 0.010 250 / 0.6)",
                border: "1px solid var(--color-hairline)",
              }}
            >
              <Search
                className="h-3 w-3 shrink-0"
                style={{ color: "var(--color-text-4)" }}
              />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={t("dashboard:episode_search_placeholder")}
                className="min-w-0 flex-1 bg-transparent text-xs outline-none focus-ring"
                style={{ color: "var(--color-text)" }}
              />
            </div>
          </div>

          <div className="flex-1 overflow-y-auto px-2 pb-2.5">
            {filteredEps.length === 0 ? (
              <div
                className="px-2 py-6 text-center text-[11px] italic"
                style={{ color: "var(--color-text-4)" }}
              >
                {t("dashboard:no_episodes_yet")}
              </div>
            ) : (
              filteredEps.map((ep) => (
                <EpisodeCard
                  key={ep.episode}
                  ep={ep}
                  active={ep.episode === activeEp}
                  onClick={() => setLocation(`/episodes/${ep.episode}`)}
                />
              ))
            )}
          </div>
        </>
      ) : (
        <div className="flex-1 overflow-y-auto px-2.5 py-1.5">
          {filteredEps.map((ep) => (
            <button
              key={ep.episode}
              type="button"
              onClick={() => setLocation(`/episodes/${ep.episode}`)}
              title={`E${ep.episode} · ${ep.title}`}
              className="num mb-[3px] flex h-9 w-full items-center justify-center rounded-md text-[11px] font-bold focus-ring"
              style={{
                background: ep.episode === activeEp ? "var(--color-accent-dim)" : "transparent",
                color:
                  ep.episode === activeEp
                    ? "var(--color-accent-2)"
                    : "var(--color-text-3)",
              }}
            >
              E{ep.episode}
            </button>
          ))}
        </div>
      )}

      {/* ---- Collapse footer ---- */}
      <div
        className="flex items-center gap-2 px-2.5 py-2"
        style={{
          borderTop: "1px solid var(--color-hairline)",
          background: "oklch(0.17 0.010 250 / 0.6)",
        }}
      >
        <button
          type="button"
          onClick={() => setCollapsed((c) => !c)}
          className="grid h-7 w-7 place-items-center rounded-md focus-ring"
          style={{
            background: "oklch(0.24 0.012 250 / 0.5)",
            color: "var(--color-text-3)",
          }}
          title={collapsed ? t("dashboard:sidebar_expand") : t("dashboard:sidebar_collapse")}
          aria-label={
            collapsed ? t("dashboard:sidebar_expand") : t("dashboard:sidebar_collapse")
          }
        >
          {collapsed ? (
            <ChevronRight className="h-3.5 w-3.5" />
          ) : (
            <ChevronLeft className="h-3.5 w-3.5" />
          )}
        </button>
      </div>
    </aside>
  );
}
