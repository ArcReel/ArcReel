import { useId, type ReactNode } from "react";
import { ArrowRight, Box, Image as ImageIcon, Lock, Play, Trees, User } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import type { GenerationRoute } from "@/utils/generation-mode";

/**
 * 生成模式二选一卡（创建向导）。
 *
 * 无预选、必选：生成模式创建后不可更改，让这个不可逆决策以对比形态呈现。
 * 卡内图示画的是两个生成模式各自喂给视频模型的输入契约——分镜图生视频是单张分镜图（I2V），
 * 参考生视频是角色/场景/道具参考图集合（R2V），这正是区分生成模式的判据。
 */

/**
 * 生成模式的文案与输入契约标签。向导二卡与设置页只读展示共用同一份，
 * 避免两处各自维护「生成模式 → 名称 / 描述 / I2V-R2V」的对应关系而漂移。
 */
export const ROUTE_META: Record<GenerationRoute, { nameKey: string; descKey: string; tag: string }> = {
  storyboard: { nameKey: "route_storyboard", descKey: "route_storyboard_desc", tag: "I2V" },
  reference_video: { nameKey: "route_reference_video", descKey: "route_reference_video_desc", tag: "R2V" },
};

/** 「创建后不可更改」琥珀锁形徽章。与区块标题同行，设置页只读展示复用。 */
export function RouteLockBadge() {
  const { t } = useTranslation("dashboard");
  return (
    <span className="inline-flex items-center gap-1 rounded-sm border border-warn/30 bg-warn/5 px-1.5 py-0.5 text-xs text-warn">
      <Lock aria-hidden className="size-3" />
      {t("generation_route_locked")}
    </span>
  );
}

function PlayFrame({ active }: { active: boolean }) {
  return (
    <span
      className={cn(
        "grid h-12 w-10 place-items-center rounded-sm border border-dashed transition-colors",
        active ? "border-primary/50" : "border-border",
      )}
    >
      <Play className={cn("size-4", active ? "fill-primary text-primary" : "fill-muted-foreground text-muted-foreground")} />
    </span>
  );
}

function frameClass(active: boolean): string {
  return active ? "border-primary/50 bg-primary/10" : "border-border bg-background/60";
}

/** 输入契约图示：单张分镜图 → 视频。 */
function StoryboardDiagram({ active }: { active: boolean }) {
  return (
    <span aria-hidden className="flex items-center gap-2.5">
      <span className={cn("grid h-12 w-10 place-items-center rounded-sm border transition-colors", frameClass(active))}>
        <ImageIcon className={cn("size-4", active ? "text-primary" : "text-muted-foreground")} />
      </span>
      <ArrowRight className="size-3.5 shrink-0 text-muted-foreground" />
      <PlayFrame active={active} />
    </span>
  );
}

/** 输入契约图示：角色、场景、道具参考图叠放 → 视频。 */
function ReferenceDiagram({ active }: { active: boolean }) {
  const iconCls = cn("size-4", active ? "text-primary" : "text-muted-foreground");
  const chip = (icon: ReactNode, key: string) => (
    <span key={key} className={cn("grid size-9 place-items-center rounded-sm border transition-colors", frameClass(active))}>
      {icon}
    </span>
  );
  return (
    <span aria-hidden className="flex items-center gap-2.5">
      <span className="flex -space-x-2.5">
        {chip(<User className={iconCls} />, "character")}
        {chip(<Trees className={iconCls} />, "scene")}
        {chip(<Box className={iconCls} />, "prop")}
      </span>
      <ArrowRight className="size-3.5 shrink-0 text-muted-foreground" />
      <PlayFrame active={active} />
    </span>
  );
}

/** 呈现顺序：分镜图生视频在左（默认路径），参考生视频在右。 */
const ROUTE_CARDS: readonly { route: GenerationRoute; Diagram: (props: { active: boolean }) => ReactNode }[] = [
  { route: "storyboard", Diagram: StoryboardDiagram },
  { route: "reference_video", Diagram: ReferenceDiagram },
];

export interface GenerationRouteCardsProps {
  /** null = 未选。必选：未选时向导不放行。 */
  value: GenerationRoute | null;
  onChange: (next: GenerationRoute) => void;
  /** 装配条等从属内容，仅分镜图生视频选中时由调用方传入。 */
  children?: ReactNode;
}

export function GenerationRouteCards({ value, onChange, children }: GenerationRouteCardsProps) {
  const { t } = useTranslation("dashboard");
  const labelId = useId();

  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-center gap-2">
        <span id={labelId} className="text-sm font-medium text-foreground">
          {t("generation_route")}
        </span>
        <RouteLockBadge />
      </div>

      <div
        role="radiogroup"
        aria-labelledby={labelId}
        aria-required="true"
        className="grid grid-cols-2 gap-2.5"
      >
        {ROUTE_CARDS.map(({ route, Diagram }) => {
          const selected = value === route;
          const meta = ROUTE_META[route];
          return (
            <label
              key={route}
              className={cn(
                "relative flex cursor-pointer flex-col items-center gap-2.5 rounded-lg border px-4 py-4 text-center transition-colors has-focus-visible:ring-3 has-focus-visible:ring-ring/50",
                selected ? "border-primary/60 bg-primary/10" : "border-border hover:bg-accent",
              )}
            >
              <input
                type="radio"
                name={labelId}
                value={route}
                checked={selected}
                onChange={() => onChange(route)}
                className="sr-only"
              />
              <Diagram active={selected} />
              <span className="text-sm font-medium text-foreground">{t(meta.nameKey)}</span>
              {/* 选中项浅底上用中间档文字，保证对比度 */}
              <span className={cn("text-xs", selected ? "text-subtle-foreground" : "text-muted-foreground")}>
                {t(meta.descKey)}
              </span>
            </label>
          );
        })}
      </div>

      {children}
    </div>
  );
}
