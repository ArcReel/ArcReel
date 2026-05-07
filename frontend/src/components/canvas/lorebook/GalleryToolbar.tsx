import { useTranslation } from "react-i18next";
import { Plus, Package } from "lucide-react";

interface Props {
  title: string;
  count: number;
  onAdd: () => void;
  onPickFromLibrary: () => void;
}

/**
 * GalleryToolbar — v3 视觉：玻璃栏 + display-serif 标题 + accent CTA。
 */
export function GalleryToolbar({ title, count, onAdd, onPickFromLibrary }: Props) {
  const { t } = useTranslation(["dashboard", "assets"]);
  return (
    <div
      className="sticky top-0 z-10 flex items-center gap-3 px-5 py-3"
      style={{
        background:
          "linear-gradient(180deg, oklch(0.20 0.012 265 / 0.85), oklch(0.18 0.010 265 / 0.65))",
        backdropFilter: "blur(10px)",
        WebkitBackdropFilter: "blur(10px)",
        borderBottom: "1px solid var(--color-hairline-soft)",
      }}
    >
      {/* Tiny accent dash before the title — establishes editorial rhythm */}
      <span
        aria-hidden
        className="h-3 w-[3px] rounded-full"
        style={{
          background:
            "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))",
          boxShadow: "0 0 8px var(--color-accent-glow)",
        }}
      />
      <h2
        className="display-serif text-[15px] font-semibold tracking-tight"
        style={{ color: "var(--color-text)" }}
      >
        {title}
      </h2>
      <span
        className="num inline-flex items-center justify-center rounded-md px-1.5 py-[2px] text-[10.5px]"
        style={{
          color: "var(--color-text-3)",
          background: "var(--color-accent-dim)",
          border: "1px solid var(--color-accent-soft)",
          minWidth: 22,
        }}
      >
        {String(count).padStart(2, "0")}
      </span>
      <div className="flex-1" />
      <button
        type="button"
        onClick={onPickFromLibrary}
        className="focus-ring inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11.5px] transition-colors"
        style={{
          color: "var(--color-text-2)",
          border: "1px solid var(--color-hairline)",
          background: "oklch(0.22 0.011 265 / 0.5)",
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = "oklch(0.26 0.013 265 / 0.7)";
          e.currentTarget.style.color = "var(--color-text)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = "oklch(0.22 0.011 265 / 0.5)";
          e.currentTarget.style.color = "var(--color-text-2)";
        }}
      >
        <Package className="h-3.5 w-3.5" />
        {t("assets:from_library")}
      </button>
      <button
        type="button"
        onClick={onAdd}
        className="focus-ring inline-flex items-center gap-1.5 rounded-md px-3 py-1 text-[11.5px] font-medium transition-transform"
        style={{
          color: "oklch(0.14 0 0)",
          background:
            "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))",
          boxShadow:
            "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 6px 18px -4px var(--color-accent-glow), 0 0 0 1px var(--color-accent-soft)",
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
    </div>
  );
}
