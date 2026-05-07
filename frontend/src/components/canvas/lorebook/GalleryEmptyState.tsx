import { Plus } from "lucide-react";

interface Props {
  icon: React.ReactNode;
  label: string;
  hint: string;
  onClick: () => void;
}

/**
 * GalleryEmptyState — 资产页空态：editorial 卡片，带累托线 + 紫色 CTA。
 */
export function GalleryEmptyState({ icon, label, hint, onClick }: Props) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="focus-ring group relative w-full overflow-hidden rounded-2xl px-8 py-16 text-center transition-colors"
      style={{
        border: "1px dashed var(--color-hairline)",
        background:
          "radial-gradient(600px 280px at 50% -10%, var(--color-accent-dim), transparent 60%), oklch(0.18 0.010 265 / 0.35)",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = "var(--color-accent-soft)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = "var(--color-hairline)";
      }}
    >
      {/* Top accent line */}
      <span
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-px"
        style={{
          background:
            "linear-gradient(90deg, transparent, var(--color-accent-soft), transparent)",
        }}
      />

      <div className="mx-auto flex max-w-md flex-col items-center gap-4">
        <span
          aria-hidden
          className="grid h-14 w-14 place-items-center rounded-2xl"
          style={{
            background:
              "linear-gradient(135deg, var(--color-accent-dim), oklch(0.76 0.09 295 / 0.04))",
            border: "1px solid var(--color-accent-soft)",
            color: "var(--color-accent-2)",
            boxShadow: "0 12px 30px -10px var(--color-accent-glow)",
          }}
        >
          {icon}
        </span>
        <div className="space-y-1">
          <div
            className="display-serif text-[18px] font-semibold tracking-tight"
            style={{ color: "var(--color-text)" }}
          >
            {label}
          </div>
          <p
            className="text-[12.5px] leading-[1.6]"
            style={{ color: "var(--color-text-3)" }}
          >
            {hint}
          </p>
        </div>
        <span
          className="mt-1 inline-flex items-center gap-1.5 rounded-md px-3 py-1 text-[11.5px] font-medium transition-transform group-hover:translate-y-[-1px]"
          style={{
            color: "oklch(0.14 0 0)",
            background:
              "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))",
            boxShadow:
              "inset 0 1px 0 oklch(1 0 0 / 0.35), 0 6px 18px -4px var(--color-accent-glow), 0 0 0 1px var(--color-accent-soft)",
          }}
        >
          <Plus className="h-3.5 w-3.5" />
          {label}
        </span>
      </div>
    </button>
  );
}
