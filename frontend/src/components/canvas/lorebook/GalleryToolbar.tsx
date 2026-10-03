import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Plus, Package } from "lucide-react";

interface Props {
  title: string;
  count: number;
  /** 未提供时隐藏「新增」入口（如只读展示的引导演示项目）。 */
  onAdd?: () => void;
  /** 未提供时隐藏「从资产库选择」入口（如不入全局库的资产类型）。 */
  onPickFromLibrary?: () => void;
  /** 标题右侧的附加控件（资产图状态筛选与批量生成入口）。 */
  children?: ReactNode;
}

/**
 * GalleryToolbar — v3 视觉：玻璃栏 + display-serif 标题 + accent CTA。
 */
export function GalleryToolbar({ title, count, onAdd, onPickFromLibrary, children }: Props) {
  const { t } = useTranslation(["dashboard", "assets"]);
  return (
    <div
      className="sticky top-0 z-10 flex items-center gap-3 px-5 py-3"
      style={{
        background:
          "linear-gradient(180deg, oklch(0.20 0.012 265 / 0.85), oklch(0.18 0.010 265 / 0.65))",
        backdropFilter: "blur(10px)",
        WebkitBackdropFilter: "blur(10px)",
        borderBottom: "1px solid color-mix(in oklab, var(--border) 50%, transparent)",
      }}
    >
      {/* Tiny accent dash before the title — establishes editorial rhythm */}
      <span
        aria-hidden
        className="h-3 w-[3px] rounded-full"
        style={{
          background:
            "var(--primary)",
          boxShadow: "0 0 8px color-mix(in oklab, var(--primary) 35%, transparent)",
        }}
      />
      <h2
        className="display-serif text-[15px] font-semibold tracking-tight"
        style={{ color: "var(--foreground)" }}
      >
        {title}
      </h2>
      <span
        className="num inline-flex items-center justify-center rounded-md px-1.5 py-[2px] text-[10.5px]"
        style={{
          color: "var(--muted-foreground)",
          background: "color-mix(in oklab, var(--primary) 12%, transparent)",
          border: "1px solid color-mix(in oklab, var(--primary) 22%, transparent)",
          minWidth: 22,
        }}
      >
        {String(count).padStart(2, "0")}
      </span>
      {children}
      <div className="flex-1" />
      {onPickFromLibrary && (
      <button
        type="button"
        onClick={onPickFromLibrary}
        className="focus-ring inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11.5px] transition-colors"
        style={{
          color: "var(--subtle-foreground)",
          border: "1px solid var(--border)",
          background: "oklch(0.22 0.011 265 / 0.5)",
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = "oklch(0.26 0.013 265 / 0.7)";
          e.currentTarget.style.color = "var(--foreground)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = "oklch(0.22 0.011 265 / 0.5)";
          e.currentTarget.style.color = "var(--subtle-foreground)";
        }}
      >
        <Package className="h-3.5 w-3.5" />
        {t("assets:from_library")}
      </button>
      )}
      {onAdd && (
      <button
        type="button"
        onClick={onAdd}
        className="focus-ring inline-flex items-center gap-1.5 rounded-md px-3 py-1 text-[11.5px] font-medium transition-transform"
        style={{
          color: "oklch(0.14 0 0)",
          background:
            "var(--primary)",
          boxShadow:
            "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 6px 18px -4px color-mix(in oklab, var(--primary) 35%, transparent), 0 0 0 1px color-mix(in oklab, var(--primary) 22%, transparent)",
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.transform = "translateY(-1px)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.transform = "translateY(0)";
        }}
      >
        <Plus className="h-3.5 w-3.5" />
        {title}
      </button>
      )}
    </div>
  );
}
