import { Package, History, PackageCheck, Scissors } from "lucide-react";
import { GlassPopover } from "@/components/ui/GlassPopover";
import { useTranslation } from "react-i18next";
import type { RefObject, ReactNode } from "react";

export type ExportScope = "current" | "full";

interface ExportScopeDialogProps {
  open: boolean;
  onClose: () => void;
  onSelect: (scope: ExportScope) => void;
  anchorRef: RefObject<HTMLElement | null>;
  /** 提示里「打开剪辑视图」链接指向的集（集 ID 与集名）；项目还没有集时为 null，只显示提示。 */
  editViewEpisode: { episode: number; name: string } | null;
  onOpenEditView: (episode: number) => void;
}

/** 顶栏「导出项目」的范围选择：只有项目归档；成片与剪映草稿在各集的剪辑视图中导出。 */
export function ExportScopeDialog({
  open,
  onClose,
  onSelect,
  anchorRef,
  editViewEpisode,
  onOpenEditView,
}: ExportScopeDialogProps) {
  const { t } = useTranslation(["dashboard", "common"]);

  return (
    <GlassPopover
      open={open}
      onClose={onClose}
      anchorRef={anchorRef}
      sideOffset={8}
      width="w-[22rem]"
    >
      <div className="px-4 pb-3 pt-3.5">
        <div className="mb-2.5 flex items-center gap-2">
          <span
            aria-hidden
            className="grid h-7 w-7 place-items-center rounded-lg"
            style={{
              background:
                "linear-gradient(135deg, var(--color-accent-dim), oklch(0.76 0.09 295 / 0.05))",
              border: "1px solid var(--color-accent-soft)",
              color: "var(--color-accent-2)",
              boxShadow: "0 8px 18px -8px var(--color-accent-glow)",
            }}
          >
            <PackageCheck className="h-3.5 w-3.5" />
          </span>
          <div className="min-w-0">
            <div
              className="display-serif text-[14px] font-semibold tracking-tight"
              style={{ color: "var(--color-text)" }}
            >
              {t("dashboard:export_scope_title")}
            </div>
            <div
              className="num text-[10px] uppercase"
              style={{
                color: "var(--color-text-4)",
                letterSpacing: "1.0px",
              }}
            >
              {t("dashboard:eyebrow_export_scope")}
            </div>
          </div>
        </div>

        <div className="flex flex-col gap-2">
          <ScopeOption
            icon={<Package className="h-4 w-4" />}
            title={
              <span className="inline-flex items-center gap-1.5">
                <span>{t("dashboard:current_version_only")}</span>
                <span
                  className="num rounded-[3px] px-1.5 py-px text-[9.5px] uppercase"
                  style={{
                    letterSpacing: "0.6px",
                    color: "var(--color-accent-2)",
                    background: "var(--color-accent-dim)",
                    border: "1px solid var(--color-accent-soft)",
                  }}
                >
                  {t("dashboard:recommended")}
                </span>
              </span>
            }
            hint={t("dashboard:small_size_hint")}
            tone="accent"
            onClick={() => onSelect("current")}
          />
          <ScopeOption
            icon={<History className="h-4 w-4" />}
            title={t("dashboard:all_data")}
            hint={t("dashboard:full_history_hint")}
            tone="neutral"
            onClick={() => onSelect("full")}
          />
        </div>

        <div
          className="mt-3 flex items-start gap-2 border-t pt-3 text-[11.5px] leading-[1.55]"
          style={{ borderColor: "var(--color-hairline-soft)", color: "var(--color-text-4)" }}
        >
          <Scissors className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <p>
            {t("dashboard:export_renders_moved_hint")}
            {editViewEpisode !== null && (
              <>
                {" "}
                <button
                  type="button"
                  onClick={() => onOpenEditView(editViewEpisode.episode)}
                  className="focus-ring rounded underline underline-offset-2"
                  style={{ color: "var(--color-accent-2)" }}
                >
                  {t("dashboard:export_open_edit_view", { name: editViewEpisode.name })}
                </button>
              </>
            )}
          </p>
        </div>
      </div>
    </GlassPopover>
  );
}

type ScopeTone = "accent" | "neutral";

const SCOPE_PALETTE: Record<
  ScopeTone,
  { color: string; ring: string; hoverBg: string; hoverBorder: string }
> = {
  accent: {
    color: "var(--color-accent-2)",
    ring: "var(--color-accent-soft)",
    hoverBg: "var(--color-accent-dim)",
    hoverBorder: "var(--color-accent-soft)",
  },
  neutral: {
    color: "var(--color-text-3)",
    ring: "var(--color-hairline)",
    hoverBg: "oklch(1 0 0 / 0.04)",
    hoverBorder: "var(--color-hairline-strong)",
  },
};

function ScopeOption({
  icon,
  title,
  hint,
  tone,
  onClick,
}: {
  icon: ReactNode;
  title: ReactNode;
  hint: string;
  tone: ScopeTone;
  onClick: () => void;
}) {
  const palette = SCOPE_PALETTE[tone];

  return (
    <button
      type="button"
      onClick={onClick}
      className="focus-ring group flex items-start gap-3 rounded-lg px-3 py-2.5 text-left transition-colors"
      style={{
        border: "1px solid var(--color-hairline)",
        background: "oklch(0.20 0.011 265 / 0.4)",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = palette.hoverBg;
        e.currentTarget.style.borderColor = palette.hoverBorder;
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = "oklch(0.20 0.011 265 / 0.4)";
        e.currentTarget.style.borderColor = "var(--color-hairline)";
      }}
    >
      <span
        aria-hidden
        className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-md"
        style={{
          background: "oklch(0.16 0.010 265 / 0.6)",
          border: `1px solid ${palette.ring}`,
          color: palette.color,
        }}
      >
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        <div
          className="text-[13px] font-medium leading-tight"
          style={{ color: "var(--color-text)" }}
        >
          {title}
        </div>
        <p
          className="mt-1 text-[11.5px] leading-[1.5]"
          style={{ color: "var(--color-text-4)" }}
        >
          {hint}
        </p>
      </div>
    </button>
  );
}
