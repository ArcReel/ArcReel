import { useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useTranslation } from "react-i18next";
import { ChevronLeft, ChevronRight, Plus, Search } from "lucide-react";
import type { NarrationSegment, DramaScene } from "@/types";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import { StatusBadge, statusFromAssets } from "./StatusBadge";

type Segment = NarrationSegment | DramaScene;

interface ShotListProps {
  segments: Segment[];
  selectedIndex: number;
  onSelect: (index: number) => void;
  contentMode: "narration" | "drama";
  projectName: string;
  collapsed: boolean;
  onToggleCollapse: () => void;
  /** 接收滚动容器 ref，外部可挂载 useScrollTarget */
  scrollContainerRef?: React.RefObject<HTMLDivElement | null>;
}

function getSegmentId(seg: Segment, mode: "narration" | "drama"): string {
  return mode === "narration"
    ? (seg as NarrationSegment).segment_id
    : (seg as DramaScene).scene_id;
}

function getSegmentText(seg: Segment, mode: "narration" | "drama"): string {
  if (mode === "narration") return (seg as NarrationSegment).novel_text || "";
  // drama 模式：显示 scene_type 或 image_prompt.scene 作为预览
  const drama = seg as DramaScene;
  if (drama.scene_type) return drama.scene_type;
  const ip = drama.image_prompt;
  if (typeof ip === "string") return ip;
  if (ip && typeof ip === "object" && "scene" in ip) return (ip as { scene: string }).scene;
  return "";
}

function getStoryboardVersionCount(seg: Segment): number {
  // 暂用 storyboard_image 是否存在作为粗略 V1 指示。真实版本数走 GET /versions API（异步），
  // 此处只显示 V1 标记或空。后续可扩展。
  return seg.generated_assets?.storyboard_image ? 1 : 0;
}

/**
 * 分镜列表（左侧 260px / 折叠态 44px）。虚拟化滚动以支持长剧集。
 */
export function ShotList({
  segments,
  selectedIndex,
  onSelect,
  contentMode,
  projectName,
  collapsed,
  onToggleCollapse,
  scrollContainerRef,
}: ShotListProps) {
  const { t } = useTranslation("dashboard");
  const [search, setSearch] = useState("");
  const internalScrollRef = useRef<HTMLDivElement>(null);
  const scrollRef = scrollContainerRef ?? internalScrollRef;

  const fingerprints = useProjectsStore((s) => s.assetFingerprints);

  const filtered = useMemo(() => {
    if (!search) return segments.map((seg, i) => ({ seg, originalIndex: i }));
    const s = search.toLowerCase();
    return segments
      .map((seg, i) => ({ seg, originalIndex: i }))
      .filter(({ seg }) => {
        const id = getSegmentId(seg, contentMode);
        const text = getSegmentText(seg, contentMode);
        return id.toLowerCase().includes(s) || text.toLowerCase().includes(s);
      });
  }, [segments, search, contentMode]);

  // eslint-disable-next-line react-hooks/incompatible-library -- useVirtualizer 与 React Compiler 不兼容（已知第三方库限制）
  const virtualizer = useVirtualizer({
    count: filtered.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 84,
    overscan: 6,
  });

  if (collapsed) {
    return (
      <div
        className="flex flex-col items-center gap-1.5 overflow-y-auto py-2.5"
        style={{
          width: 44,
          borderRight: "1px solid var(--color-hairline)",
          background: "oklch(0.19 0.011 265 / 0.5)",
        }}
      >
        <button
          type="button"
          onClick={onToggleCollapse}
          title={t("shot_list_expand")}
          aria-label={t("shot_list_expand")}
          className="grid h-7 w-7 place-items-center rounded-md focus-ring"
          style={{
            background: "oklch(0.24 0.012 265 / 0.5)",
            border: "1px solid var(--color-hairline-soft)",
            color: "var(--color-text-3)",
          }}
        >
          <ChevronRight className="h-3.5 w-3.5" />
        </button>
        <div
          className="mt-1.5 text-[9.5px] font-bold uppercase"
          style={{
            color: "var(--color-text-4)",
            letterSpacing: "1.2px",
            writingMode: "vertical-rl",
            transform: "rotate(180deg)",
            fontFamily: "var(--font-mono)",
          }}
        >
          SHOTS · {segments.length}
        </div>
        <div className="mt-1 flex flex-1 flex-col items-center gap-1">
          {segments.map((s, i) => {
            const id = getSegmentId(s, contentMode);
            return (
              <button
                key={id}
                type="button"
                onClick={() => onSelect(i)}
                title={id}
                className="num grid h-7 w-7 place-items-center rounded-[5px] text-[9.5px] font-bold focus-ring"
                style={{
                  color: i === selectedIndex ? "oklch(0.14 0 0)" : "var(--color-text-3)",
                  background:
                    i === selectedIndex
                      ? "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))"
                      : "oklch(0.22 0.011 265 / 0.5)",
                  border: "1px solid var(--color-hairline-soft)",
                }}
              >
                {id.length > 4 ? id.slice(-3) : id}
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  return (
    <div
      style={{
        borderRight: "1px solid var(--color-hairline)",
        background:
          "linear-gradient(180deg, oklch(0.19 0.011 265 / 0.5), oklch(0.17 0.010 265 / 0.35))",
      }}
      className="flex h-full min-w-0 flex-col"
    >
      <div className="flex shrink-0 items-center gap-2 px-3 pb-2 pt-3">
        <span
          className="text-[10.5px] font-bold uppercase"
          style={{ color: "var(--color-text-4)", letterSpacing: "0.8px" }}
        >
          {t("shots_section_title")}
        </span>
        <span className="num text-[10px]" style={{ color: "var(--color-text-4)" }}>
          {filtered.length}
        </span>
        <span className="flex-1" />
        <button
          type="button"
          onClick={onToggleCollapse}
          title={t("shot_list_collapse")}
          aria-label={t("shot_list_collapse")}
          className="grid h-6 w-6 place-items-center rounded text-[11px] focus-ring"
          style={{ color: "var(--color-text-4)" }}
        >
          <ChevronLeft className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          className="sv-navbtn inline-flex items-center gap-1 px-2"
        >
          <Plus className="h-3 w-3" />
          <span>{t("add_episode")}</span>
        </button>
      </div>

      <div className="shrink-0 px-3 pb-2.5">
        <div
          className="flex items-center gap-1.5 rounded-md px-2 py-1.5"
          style={{
            background: "oklch(0.20 0.011 265 / 0.55)",
            border: "1px solid var(--color-hairline-soft)",
          }}
        >
          <Search
            className="h-3 w-3 shrink-0"
            style={{ color: "var(--color-text-4)" }}
          />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("shot_search_placeholder")}
            className="min-w-0 flex-1 bg-transparent text-[11.5px] outline-none focus-ring"
            style={{ color: "var(--color-text-2)" }}
          />
        </div>
      </div>

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-2 pb-2.5">
        <div
          className="relative"
          style={{ height: `${virtualizer.getTotalSize()}px` }}
        >
          {virtualizer.getVirtualItems().map((virt) => {
            const { seg, originalIndex } = filtered[virt.index];
            const id = getSegmentId(seg, contentMode);
            const text = getSegmentText(seg, contentMode);
            const status = statusFromAssets(seg.generated_assets?.status);
            const versions = getStoryboardVersionCount(seg);
            const active = originalIndex === selectedIndex;
            const sbPath = seg.generated_assets?.storyboard_image;
            const sbFp = sbPath ? (fingerprints[sbPath] ?? null) : null;
            const sbUrl = sbPath ? API.getFileUrl(projectName, sbPath, sbFp) : null;

            return (
              <button
                key={id}
                id={`segment-${id}`}
                type="button"
                onClick={() => onSelect(originalIndex)}
                ref={virtualizer.measureElement}
                data-index={virt.index}
                className="absolute left-0 right-0 grid w-full items-center gap-2.5 rounded-lg p-2 text-left transition-colors focus-ring"
                style={{
                  gridTemplateColumns: "auto 1fr",
                  transform: `translateY(${virt.start}px)`,
                  background: active
                    ? "linear-gradient(180deg, oklch(0.26 0.018 290 / 0.5), oklch(0.22 0.015 280 / 0.35))"
                    : "transparent",
                  border: active
                    ? "1px solid var(--color-accent-soft)"
                    : "1px solid transparent",
                  boxShadow: active
                    ? "0 0 0 1px var(--color-accent-soft), 0 4px 12px -6px oklch(0 0 0 / 0.4)"
                    : "none",
                }}
                onMouseEnter={(e) => {
                  if (!active)
                    e.currentTarget.style.background = "oklch(0.24 0.012 265 / 0.4)";
                }}
                onMouseLeave={(e) => {
                  if (!active) e.currentTarget.style.background = "transparent";
                }}
              >
                {active && (
                  <span
                    aria-hidden="true"
                    className="absolute -left-px top-2 bottom-2 w-0.5 rounded"
                    style={{
                      background: "var(--color-accent)",
                      boxShadow: "0 0 8px var(--color-accent-glow)",
                    }}
                  />
                )}
                <div
                  className="relative shrink-0 overflow-hidden rounded-[5px]"
                  style={{ width: 48, height: 64 }}
                >
                  {sbUrl ? (
                    <img
                      src={sbUrl}
                      alt={id}
                      className="h-full w-full object-cover"
                      loading="lazy"
                    />
                  ) : (
                    <div
                      className="flex h-full w-full items-center justify-center"
                      style={{
                        background:
                          "linear-gradient(135deg, oklch(0.30 0.05 280), oklch(0.18 0.02 260))",
                      }}
                    />
                  )}
                  <span
                    className="num absolute bottom-0.5 left-1 text-[9px] font-bold"
                    style={{
                      color: "oklch(0.98 0 0)",
                      textShadow: "0 1px 2px oklch(0 0 0 / 0.8)",
                    }}
                  >
                    {id.length > 4 ? id.slice(-3) : id}
                  </span>
                </div>
                <div className="min-w-0">
                  <div
                    className="text-[12px]"
                    style={{
                      color: active ? "var(--color-text)" : "var(--color-text-2)",
                      fontWeight: active ? 600 : 500,
                      lineHeight: 1.4,
                      display: "-webkit-box",
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: "vertical",
                      overflow: "hidden",
                    }}
                  >
                    {text || id}
                  </div>
                  <div className="mt-1 flex items-center gap-1.5">
                    <StatusBadge status={status} />
                    <span className="num text-[10px]" style={{ color: "var(--color-text-4)" }}>
                      {seg.duration_seconds}s
                    </span>
                    {versions > 0 && (
                      <span
                        className="num text-[10px]"
                        style={{ color: "var(--color-text-4)" }}
                      >
                        · V{versions}
                      </span>
                    )}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
