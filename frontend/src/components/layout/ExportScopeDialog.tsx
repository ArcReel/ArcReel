import { useState, useEffect } from "react";
import {
  Package,
  History,
  Clapperboard,
  ArrowLeft,
  Loader2,
  PackageCheck,
} from "lucide-react";
import { Popover } from "@/components/ui/Popover";
import { useTranslation } from "react-i18next";
import type { RefObject, ReactNode } from "react";
import type { EpisodeMeta } from "@/types/project";
import { WARM_TONE } from "@/utils/severity-tone";

export type ExportScope = "current" | "full" | "jianying-draft";

const DRAFT_PATH_STORAGE_KEY = "arcreel_jianying_draft_path";

interface ExportScopeDialogProps {
  open: boolean;
  onClose: () => void;
  onSelect: (scope: ExportScope) => void;
  anchorRef: RefObject<HTMLElement | null>;
  episodes?: EpisodeMeta[];
  onJianyingExport?: (episode: number, draftPath: string, jianyingVersion: string) => void;
  jianyingExporting?: boolean;
}

const PANEL_BG =
  "linear-gradient(180deg, oklch(0.21 0.012 265 / 0.96), oklch(0.18 0.010 265 / 0.96))";

export function ExportScopeDialog({
  open,
  onClose,
  onSelect,
  anchorRef,
  episodes = [],
  onJianyingExport,
  jianyingExporting = false,
}: ExportScopeDialogProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [mode, setMode] = useState<"select" | "jianying-form">("select");
  const [selectedEpisode, setSelectedEpisode] = useState<number>(
    episodes.length > 0 ? episodes[0].episode : 1,
  );
  const isWindows =
    typeof navigator !== "undefined" && navigator.userAgent.includes("Windows");
  const defaultDraftPath = isWindows
    ? t("dashboard:draft_path_default_windows")
    : t("dashboard:draft_path_default_mac");
  const [draftPath, setDraftPath] = useState<string>(
    () => localStorage.getItem(DRAFT_PATH_STORAGE_KEY) || defaultDraftPath,
  );
  const [jianyingVersion, setJianyingVersion] = useState("6");

  useEffect(() => {
    if (!open) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- 弹窗关闭时重置到初始选择界面，是有意的 UI 状态重置
      setMode("select");
    }
  }, [open]);

  useEffect(() => {
    if (episodes.length > 0) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- episodes prop 变化时同步表单默认值，受控拷贝是有意设计
      setSelectedEpisode(episodes[0].episode);
    }
  }, [episodes]);

  const handleJianyingSubmit = () => {
    if (!draftPath.trim() || !onJianyingExport) return;
    localStorage.setItem(DRAFT_PATH_STORAGE_KEY, draftPath.trim());
    onJianyingExport(selectedEpisode, draftPath.trim(), jianyingVersion);
  };

  return (
    <Popover
      open={open}
      onClose={onClose}
      anchorRef={anchorRef}
      sideOffset={8}
      width="w-[22rem]"
      backgroundColor="transparent"
      className="overflow-hidden rounded-xl"
      style={{
        background: PANEL_BG,
        border: "1px solid var(--color-hairline)",
        backdropFilter: "blur(14px)",
        WebkitBackdropFilter: "blur(14px)",
        boxShadow:
          "0 24px 60px -12px oklch(0 0 0 / 0.55), inset 0 1px 0 oklch(1 0 0 / 0.05)",
      }}
    >
      <span
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 h-px"
        style={{
          background:
            "linear-gradient(90deg, transparent, var(--color-accent-soft), transparent)",
        }}
      />

      {mode === "select" ? (
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
                Export · Scope
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
            <ScopeOption
              icon={<Clapperboard className="h-4 w-4" />}
              title={t("dashboard:export_jianying_draft")}
              hint={t("dashboard:generate_jianying_zip_hint")}
              tone="warm"
              onClick={() => setMode("jianying-form")}
            />
          </div>
        </div>
      ) : (
        <div className="px-4 pb-4 pt-3.5">
          <div className="mb-3 flex items-center gap-2">
            <button
              type="button"
              onClick={() => setMode("select")}
              className="focus-ring grid h-6 w-6 place-items-center rounded-md transition-colors"
              style={{ color: "var(--color-text-3)" }}
              onMouseEnter={(e) => {
                e.currentTarget.style.color = "var(--color-text)";
                e.currentTarget.style.background = "oklch(1 0 0 / 0.05)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.color = "var(--color-text-3)";
                e.currentTarget.style.background = "transparent";
              }}
              aria-label={t("common:back")}
            >
              <ArrowLeft className="h-3.5 w-3.5" />
            </button>
            <span
              aria-hidden
              className="grid h-7 w-7 place-items-center rounded-lg"
              style={{
                background:
                  "linear-gradient(135deg, var(--color-warm-tint), var(--color-warm-tint-faint))",
                border: `1px solid ${WARM_TONE.ring}`,
                color: WARM_TONE.color,
                boxShadow: `0 8px 18px -8px ${WARM_TONE.glow}`,
              }}
            >
              <Clapperboard className="h-3.5 w-3.5" />
            </span>
            <div
              className="display-serif text-[14px] font-semibold tracking-tight"
              style={{ color: "var(--color-text)" }}
            >
              {t("dashboard:export_jianying_draft")}
            </div>
          </div>
          <div className="flex flex-col gap-3">
            {episodes.length > 1 && (
              <FormField
                htmlFor="jianying-episode-select"
                label={t("dashboard:select_episode")}
              >
                <select
                  id="jianying-episode-select"
                  value={selectedEpisode}
                  onChange={(e) => setSelectedEpisode(Number(e.target.value))}
                  className="focus-ring w-full rounded-md px-2.5 py-1.5 text-[13px] outline-none"
                  style={{
                    background: "oklch(0.16 0.010 265 / 0.6)",
                    border: "1px solid var(--color-hairline)",
                    color: "var(--color-text)",
                  }}
                >
                  {episodes.map((ep) => (
                    <option key={ep.episode} value={ep.episode}>
                      {t("dashboard:episode_with_title", {
                        episode: ep.episode,
                        title: ep.title,
                      })}
                    </option>
                  ))}
                </select>
              </FormField>
            )}

            <FormField
              htmlFor="jianying-version-select"
              label={t("dashboard:jianying_version")}
            >
              <select
                id="jianying-version-select"
                value={jianyingVersion}
                onChange={(e) => setJianyingVersion(e.target.value)}
                className="focus-ring w-full rounded-md px-2.5 py-1.5 text-[13px] outline-none"
                style={{
                  background: "oklch(0.16 0.010 265 / 0.6)",
                  border: "1px solid var(--color-hairline)",
                  color: "var(--color-text)",
                }}
              >
                <option value="6">{t("dashboard:jianying_v6_plus")}</option>
                <option value="5">{t("dashboard:jianying_v5_x")}</option>
              </select>
            </FormField>

            <FormField
              htmlFor="jianying-draft-path"
              label={t("dashboard:draft_path")}
              hint={t("dashboard:draft_path_hint")}
            >
              <input
                id="jianying-draft-path"
                type="text"
                value={draftPath}
                onChange={(e) => setDraftPath(e.target.value)}
                placeholder={t("dashboard:draft_path_placeholder")}
                className="focus-ring w-full rounded-md px-2.5 py-1.5 text-[13px] outline-none"
                style={{
                  background: "oklch(0.16 0.010 265 / 0.6)",
                  border: "1px solid var(--color-hairline)",
                  color: "var(--color-text)",
                  fontFamily: "var(--font-mono)",
                }}
              />
            </FormField>

            <button
              type="button"
              onClick={handleJianyingSubmit}
              disabled={!draftPath.trim() || jianyingExporting}
              className="focus-ring inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-2 text-[12.5px] font-medium transition-transform disabled:cursor-not-allowed disabled:opacity-50"
              style={{
                color: "oklch(0.14 0 0)",
                background: `linear-gradient(135deg, var(--color-warm-bright), ${WARM_TONE.color})`,
                boxShadow: `inset 0 1px 0 oklch(1 0 0 / 0.35), 0 6px 18px -6px ${WARM_TONE.glow}, 0 0 0 1px ${WARM_TONE.ring}`,
              }}
              onMouseEnter={(e) => {
                if (!e.currentTarget.disabled)
                  e.currentTarget.style.transform = "translateY(-1px)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.transform = "translateY(0)";
              }}
            >
              {jianyingExporting ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  {t("dashboard:exporting")}
                </>
              ) : (
                t("dashboard:export_draft")
              )}
            </button>
          </div>
        </div>
      )}
    </Popover>
  );
}

type ScopeTone = "accent" | "neutral" | "warm";

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
  warm: {
    color: WARM_TONE.color,
    ring: WARM_TONE.ring,
    hoverBg: WARM_TONE.soft,
    hoverBorder: WARM_TONE.ring,
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

function FormField({
  htmlFor,
  label,
  hint,
  children,
}: {
  htmlFor: string;
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div>
      <label
        htmlFor={htmlFor}
        className="num mb-1 block text-[10px] uppercase"
        style={{
          color: "var(--color-text-4)",
          letterSpacing: "1.0px",
        }}
      >
        {label}
      </label>
      {children}
      {hint && (
        <p
          className="mt-1.5 text-[11px] leading-[1.55]"
          style={{ color: "var(--color-text-4)" }}
        >
          {hint}
        </p>
      )}
    </div>
  );
}
