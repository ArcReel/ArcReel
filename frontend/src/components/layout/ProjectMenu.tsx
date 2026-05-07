import { useEffect, useRef, useState } from "react";
import { useLocation } from "wouter";
import { ChevronDown, Plus, SlidersHorizontal } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useProjectsStore } from "@/stores/projects-store";

/**
 * 顶栏左上的项目切换菜单。
 *
 * 触发器 = 头像（项目名首字 + accent 紫梯度）+ 项目名（display-serif）
 *          + DRAMA·9:16 模式徽标 + chevron。
 * 下拉 = 当前项目卡片 + 新建项目 / 项目设置 操作项。
 */
export function ProjectMenu() {
  const { t } = useTranslation(["dashboard", "common"]);
  const [, setLocation] = useLocation();
  const { currentProjectData, currentProjectName } = useProjectsStore();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const projectTitle =
    currentProjectData?.title?.trim() || currentProjectName || t("no_project_selected");
  const initial = (projectTitle || "?").slice(0, 1).toUpperCase();
  const contentMode = currentProjectData?.content_mode;
  const aspectRatio =
    typeof currentProjectData?.aspect_ratio === "string"
      ? currentProjectData.aspect_ratio
      : currentProjectData?.aspect_ratio?.storyboard;
  const modeLabel = contentMode === "drama" ? "DRAMA" : "NARRATION";
  const modeTagline = aspectRatio ? `${modeLabel} · ${aspectRatio}` : modeLabel;

  return (
    <div ref={ref} className="relative min-w-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex min-w-0 items-center gap-2 rounded-md py-[3px] pl-1 pr-2 transition-colors focus-ring"
        style={{ background: open ? "oklch(0.26 0.012 265 / 0.5)" : "transparent" }}
        onMouseEnter={(e) => {
          if (!open) e.currentTarget.style.background = "oklch(0.26 0.012 265 / 0.5)";
        }}
        onMouseLeave={(e) => {
          if (!open) e.currentTarget.style.background = "transparent";
        }}
      >
        <div
          className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-[11.5px] font-bold display-serif"
          style={{
            background: "linear-gradient(135deg, var(--color-accent) 0%, oklch(0.55 0.12 260) 100%)",
            color: "oklch(0.12 0 0)",
            boxShadow:
              "inset 0 1px 0 oklch(1 0 0 / 0.25), inset 0 -1px 0 oklch(0 0 0 / 0.15), 0 0 0 1px oklch(1 0 0 / 0.08), 0 2px 10px -2px var(--color-accent-glow)",
          }}
        >
          {initial}
        </div>
        <div className="min-w-0 text-left">
          <div
            className="display-serif truncate text-[13.5px] font-semibold leading-[1.15]"
            style={{ letterSpacing: "-0.1px" }}
          >
            {projectTitle}
          </div>
          {currentProjectData && (
            <div
              className="mt-0.5 text-[10px] font-medium uppercase leading-[1.1]"
              style={{ color: "var(--color-text-4)", letterSpacing: "0.2px" }}
            >
              {modeTagline}
            </div>
          )}
        </div>
        <span
          className="ml-0.5 transition-transform"
          style={{
            color: "var(--color-text-4)",
            transform: open ? "rotate(180deg)" : "none",
          }}
        >
          <ChevronDown className="h-3.5 w-3.5" />
        </span>
      </button>

      {open && (
        <div
          className="absolute left-0 z-50 min-w-[280px] rounded-[10px] p-1.5"
          style={{
            top: "calc(100% + 6px)",
            background: "oklch(0.20 0.011 265 / 0.98)",
            backdropFilter: "blur(20px) saturate(1.2)",
            WebkitBackdropFilter: "blur(20px) saturate(1.2)",
            border: "1px solid var(--color-hairline-strong)",
            boxShadow:
              "0 14px 40px -10px oklch(0 0 0 / 0.6), 0 0 0 1px oklch(1 0 0 / 0.04)",
          }}
        >
          <div
            className="num px-2.5 pb-1 pt-1.5 text-[9.5px] font-bold uppercase"
            style={{ color: "var(--color-text-4)", letterSpacing: "1.2px" }}
          >
            {t("dashboard:project_switcher_current")}
          </div>
          <div
            className="flex items-center gap-2.5 rounded-md p-2"
            style={{
              background: "var(--color-accent-dim)",
              border: "1px solid var(--color-accent)",
            }}
          >
            <div
              className="display-serif grid h-[30px] w-[30px] shrink-0 place-items-center rounded-md text-sm font-bold"
              style={{ background: "var(--color-accent)", color: "oklch(0.12 0 0)" }}
            >
              {initial}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5">
                <span
                  className="text-[13px] font-semibold"
                  style={{ color: "var(--color-accent-2)" }}
                >
                  {projectTitle}
                </span>
                <span
                  className="num rounded-[3px] px-1 py-px text-[9.5px] font-bold"
                  style={{
                    background: "var(--color-accent)",
                    color: "oklch(0.12 0 0)",
                    letterSpacing: "0.4px",
                  }}
                >
                  {t("dashboard:project_switcher_active_tag")}
                </span>
              </div>
              {currentProjectData && (
                <div
                  className="mt-0.5 text-[10.5px] leading-[1.3]"
                  style={{ color: "var(--color-text-4)" }}
                >
                  {modeTagline}
                </div>
              )}
            </div>
          </div>
          <div
            className="mx-1.5 my-1 h-px"
            style={{ background: "var(--color-hairline-soft)" }}
          />
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              setLocation("~/app/projects");
            }}
            className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[12px] transition-colors focus-ring"
            style={{ color: "var(--color-text-3)" }}
            onMouseEnter={(e) =>
              (e.currentTarget.style.background = "oklch(0.26 0.012 265 / 0.55)")
            }
            onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
          >
            <Plus
              className="h-3.5 w-3.5 shrink-0"
              style={{ color: "var(--color-text-4)" }}
            />
            <span>{t("dashboard:project_switcher_new")}</span>
          </button>
          <button
            type="button"
            disabled={!currentProjectName}
            onClick={() => {
              if (!currentProjectName) return;
              setOpen(false);
              setLocation(
                `~/app/projects/${encodeURIComponent(currentProjectName)}/settings`,
              );
            }}
            className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[12px] transition-colors focus-ring disabled:opacity-50"
            style={{ color: "var(--color-text-3)" }}
            onMouseEnter={(e) => {
              if (!currentProjectName) return;
              e.currentTarget.style.background = "oklch(0.26 0.012 265 / 0.55)";
            }}
            onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
          >
            <SlidersHorizontal
              className="h-3.5 w-3.5 shrink-0"
              style={{ color: "var(--color-text-4)" }}
            />
            <span>{t("dashboard:project_switcher_settings")}</span>
          </button>
        </div>
      )}
    </div>
  );
}
