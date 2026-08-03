import type { CSSProperties, ReactNode } from "react";
import { ArrowRight, Box, Image as ImageIcon, Lock, Play, Trees, User } from "lucide-react";
import { useTranslation } from "react-i18next";
import { FieldLabel } from "@/components/ui/FieldLabel";
import type { GenerationRoute } from "@/utils/generation-mode";

/**
 * 生成路线二选一卡（创建向导）。
 *
 * 单框中缝分屏、无预选、必选：路线创建后不可更改，让这个不可逆决策以对比形态呈现。
 * 卡内图示画的是两条路线各自喂给视频模型的输入契约——分镜路线是单张分镜图（I2V），
 * 参考路线是角色/场景/道具参考图集合（R2V），这正是区分路线的判据。
 */

const ROUTE_FRAME_STYLE: CSSProperties = {
  background: "linear-gradient(180deg, oklch(0.19 0.011 268 / 0.6), oklch(0.15 0.010 262 / 0.6))",
};

/**
 * 路线的文案与输入契约标签。向导二卡与设置页只读展示共用同一份，
 * 避免两处各自维护「路线 → 名称 / 描述 / I2V-R2V」的对应关系而漂移。
 */
export const ROUTE_META: Record<GenerationRoute, { nameKey: string; descKey: string; tag: string }> = {
  storyboard: { nameKey: "route_storyboard", descKey: "route_storyboard_desc", tag: "I2V" },
  reference_video: { nameKey: "route_reference_video", descKey: "route_reference_video_desc", tag: "R2V" },
};

/** 「创建后不可更改」琥珀锁形徽章。与区块标题同行，设置页只读展示复用。 */
export function RouteLockBadge() {
  const { t } = useTranslation("dashboard");
  return (
    <span className="inline-flex items-center gap-1 rounded-[5px] border border-warm-ring bg-warm-tint-faint px-1.5 py-[3px] font-mono text-[9px] font-bold uppercase tracking-[0.1em] text-warm">
      <Lock aria-hidden className="h-2.5 w-2.5" />
      {t("generation_route_locked")}
    </span>
  );
}

function PlayFrame({ active }: { active: boolean }) {
  return (
    <span
      className={`grid h-12 w-[38px] place-items-center rounded-[4px] border border-dashed transition-colors ${
        active ? "border-accent/50" : "border-hairline"
      }`}
    >
      <Play className={`h-4 w-4 ${active ? "fill-accent-2 text-accent-2" : "fill-text-4 text-text-4"}`} />
    </span>
  );
}

/** 输入契约图示：单张分镜帧（胶片框）→ 视频。 */
function StoryboardDiagram({ active }: { active: boolean }) {
  const frameCls = active ? "border-accent/50 bg-accent-dim" : "border-hairline bg-bg/60";
  return (
    <span aria-hidden className="flex items-center gap-2.5">
      <span className={`relative block h-12 w-[38px] rounded-[4px] border ${frameCls} transition-colors`}>
        {/* sprocket 孔 — 呼应向导步骤条的胶片语汇 */}
        {[0, 1, 2].map((i) => (
          <span key={i}>
            <span
              className="absolute left-[3px] h-[3px] w-[3px] rounded-[1px] bg-hairline-strong"
              style={{ top: 8 + i * 14 }}
            />
            <span
              className="absolute right-[3px] h-[3px] w-[3px] rounded-[1px] bg-hairline-strong"
              style={{ top: 8 + i * 14 }}
            />
          </span>
        ))}
        <ImageIcon
          className={`absolute left-1/2 top-1/2 h-4 w-4 -translate-x-1/2 -translate-y-1/2 ${active ? "text-accent-2" : "text-text-4"}`}
        />
      </span>
      <ArrowRight className="h-3.5 w-3.5 shrink-0 text-text-4" />
      <PlayFrame active={active} />
    </span>
  );
}

/** 输入契约图示：角色/场景/道具参考图扇形堆叠 → 视频。 */
function ReferenceDiagram({ active }: { active: boolean }) {
  const iconCls = `h-4 w-4 ${active ? "text-accent-2" : "text-text-4"}`;
  const chip = (icon: ReactNode, i: number) => (
    <span
      key={i}
      className={`grid h-9 w-9 place-items-center rounded-[6px] border transition-colors ${
        active ? "border-accent/50 bg-accent-dim" : "border-hairline bg-bg/60"
      }`}
      style={{ transform: `rotate(${(i - 1) * 5}deg) translateY(${i === 1 ? -2 : 2}px)` }}
    >
      {icon}
    </span>
  );
  return (
    <span aria-hidden className="flex items-center gap-2.5">
      <span className="flex -space-x-2.5">
        {chip(<User className={iconCls} />, 0)}
        {chip(<Trees className={iconCls} />, 1)}
        {chip(<Box className={iconCls} />, 2)}
      </span>
      <ArrowRight className="h-3.5 w-3.5 shrink-0 text-text-4" />
      <PlayFrame active={active} />
    </span>
  );
}

/** 左右两半的呈现顺序：分镜路线在左（默认路径），参考路线在右。 */
const ROUTE_CARDS: readonly { route: GenerationRoute; Diagram: (props: { active: boolean }) => ReactNode }[] = [
  { route: "storyboard", Diagram: StoryboardDiagram },
  { route: "reference_video", Diagram: ReferenceDiagram },
];

export interface GenerationRouteCardsProps {
  /** null = 未选。必选：未选时向导不放行。 */
  value: GenerationRoute | null;
  onChange: (next: GenerationRoute) => void;
  /** 装配条等从属内容，仅分镜路线选中时由调用方传入。 */
  children?: ReactNode;
}

export function GenerationRouteCards({ value, onChange, children }: GenerationRouteCardsProps) {
  const { t } = useTranslation("dashboard");
  const sb = value === "storyboard";
  const halfCls = (selected: boolean) =>
    `relative flex cursor-pointer flex-col items-center gap-2.5 px-4 py-5 text-center transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-accent ${
      selected ? "bg-accent-dim" : "hover:bg-bg-grad-a/60"
    }`;
  const tagCls = (selected: boolean) =>
    `font-mono text-[9.5px] font-bold uppercase tracking-[0.16em] ${selected ? "text-accent-2" : "text-text-4"}`;

  return (
    <div className="space-y-2.5">
      <div className="flex items-center gap-2">
        <FieldLabel className="mb-0" required>
          {t("generation_route")}
        </FieldLabel>
        <RouteLockBadge />
      </div>

      <div
        role="radiogroup"
        aria-label={t("generation_route")}
        aria-required="true"
        className="relative grid grid-cols-2 overflow-hidden rounded-[12px] border border-hairline"
        style={ROUTE_FRAME_STYLE}
      >
        {/* 中缝 */}
        <div aria-hidden className="pointer-events-none absolute inset-y-0 left-1/2 w-px bg-hairline" />
        {/* 选中侧内描边 — 在两半之间滑动 */}
        {value ? (
          <div
            aria-hidden
            // 滑动只走 translate：动画 left 会逐帧触发重排
            className="pointer-events-none absolute inset-y-0 left-0 w-1/2 border-2 border-accent/45 transition-[translate] duration-300 motion-reduce:transition-none"
            style={{
              translate: sb ? "0" : "100%",
              borderRadius: sb ? "12px 0 0 12px" : "0 12px 12px 0",
              boxShadow: "inset 0 0 30px -18px var(--color-accent-glow)",
            }}
          />
        ) : null}

        {ROUTE_CARDS.map(({ route, Diagram }) => {
          const selected = value === route;
          const meta = ROUTE_META[route];
          return (
            <label key={route} className={halfCls(selected)}>
              <input
                type="radio"
                name="generationRoute"
                value={route}
                checked={selected}
                onChange={() => onChange(route)}
                className="sr-only"
              />
              <span className={tagCls(selected)}>{meta.tag}</span>
              <Diagram active={selected} />
              <span className="text-[14.5px] font-semibold text-text">{t(meta.nameKey)}</span>
              <span className="text-[11.5px] leading-[1.55] text-text-3">{t(meta.descKey)}</span>
            </label>
          );
        })}
      </div>

      {children}
    </div>
  );
}
