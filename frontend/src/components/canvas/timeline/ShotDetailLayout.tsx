import { useId, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { useContainerNarrow } from "@/hooks/useContainerNarrow";

/**
 * 分镜详情的栏位：页头在上，中间是「编辑栏 + 媒体栏」两栏，各自滚动，未保存提示条在最下方。
 * 不做拖拽调宽，按分镜详情自身的宽度切换：窄于 480px 时（如演示工作台在紧凑档里，Agent 面板
 * 不覆盖画布）上下排成一栏、整体滚动；480–860px 编辑栏占满、媒体栏 300px；
 * 再宽时编辑栏 360–560px，多出的宽度全部给媒体栏。
 */
export function ShotDetailLayout({
  header,
  footer,
  main,
  media,
}: {
  header: ReactNode;
  /** 两栏下方横跨整宽的未保存提示条：出现时不推动正在编辑的字段。 */
  footer?: ReactNode;
  main: ReactNode;
  media: ReactNode;
}) {
  return (
    <div className="flex min-h-0 min-w-0 flex-col">
      {header}
      <div className="@container/shot-detail flex min-h-0 flex-1 flex-col">
        <div className="relative grid min-h-0 flex-1 auto-rows-max grid-cols-[minmax(0,1fr)] content-start overflow-y-auto [scrollbar-gutter:stable] @min-[480px]/shot-detail:grid-cols-[minmax(0,1fr)_300px] @min-[480px]/shot-detail:grid-rows-[minmax(0,1fr)] @min-[480px]/shot-detail:content-stretch @min-[480px]/shot-detail:overflow-visible @min-[860px]/shot-detail:grid-cols-[minmax(360px,560px)_minmax(0,1fr)]">
          <div className="relative min-h-0 @min-[480px]/shot-detail:overflow-y-auto @min-[480px]/shot-detail:[scrollbar-gutter:stable]">
            <div className="flex flex-col gap-5 px-5 pt-4 pb-8">{main}</div>
          </div>
          <div className="@container/shot-media relative min-h-0 border-t border-border/50 px-4 pt-4 pb-8 @min-[480px]/shot-detail:overflow-y-auto @min-[480px]/shot-detail:border-t-0 @min-[480px]/shot-detail:border-l">
            {media}
          </div>
        </div>
      </div>
      {footer}
    </div>
  );
}

/** 编辑栏里的一组内容；组与组之间用分隔线隔开。 */
export function ShotGroup({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-col gap-4 border-t border-border/50 pt-5 first:border-t-0 first:pt-0">{children}</div>
  );
}

/**
 * 编辑栏里的一段：小标题加内容。给了 `htmlFor` 时标题是对应输入框的 label，
 * 否则是一个三级标题；`actions` 放在标题行右侧。
 *
 * `shortTitle`：这一段的可用宽度窄到放不下整行标题时改用它，避免长标题被挤成逐字竖排。
 * 换文案需要按实测宽度切换（容器查询只能换样式）。阈值 360px 的取法：整行标题约 96px，
 * 右侧操作组（字数、预览、形态开关）在 12px 字号下需要 200px 以上才能排成一行，
 * 两者加上图标与间距已接近 360px；低于这个宽度时标题只能靠挤压让位，最终竖排。
 * 因此触发条件只看本段实测宽度，不看视口档位——分镜列表展开、Agent 面板展开都会让
 * 编辑栏变窄，此时桌面档同样需要短标题。
 */
export function ShotSection({
  title,
  shortTitle,
  icon,
  htmlFor,
  actions,
  children,
}: {
  title: string;
  shortTitle?: string;
  icon?: ReactNode;
  htmlFor?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  const titleId = useId();
  const [ref, narrow] = useContainerNarrow<HTMLElement>(360);
  const text = narrow && shortTitle ? shortTitle : title;
  const heading = htmlFor ? (
    <label id={titleId} htmlFor={htmlFor} className="shrink-0 text-xs font-medium whitespace-nowrap text-muted-foreground">
      {text}
    </label>
  ) : (
    <h3 id={titleId} className="shrink-0 text-xs font-medium whitespace-nowrap text-muted-foreground">
      {text}
    </h3>
  );
  return (
    <section ref={ref} aria-labelledby={titleId} className="flex min-w-0 flex-col gap-2">
      {/* 标题固定单行不参与压缩；右侧操作组自己折行，窄栏里字数、预览、形态开关
          逐个换到下一行，标题与字数不再被挤成逐字竖排，也不会撑出容器右缘。 */}
      <div className="flex min-h-7 items-start gap-1.5">
        <div className="flex min-w-0 items-center gap-1.5">
          {icon ? <span className="flex shrink-0 text-muted-foreground">{icon}</span> : null}
          {heading}
        </div>
        {actions ? <div className="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-1">{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}

/** 默认折叠的「对应原文与参考」。 */
export function ShotSourceCollapsible({ children }: { children: ReactNode }) {
  const { t } = useTranslation("dashboard");
  return (
    <Collapsible>
      <CollapsibleTrigger render={<Button variant="ghost" size="sm" className="-ml-2" />}>
        <ChevronRight aria-hidden data-icon="inline-start" className="transition-transform group-aria-expanded/button:rotate-90" />
        {t("detail_section_source_and_refs")}
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="flex flex-col gap-4 pt-3">{children}</div>
      </CollapsibleContent>
    </Collapsible>
  );
}

/**
 * 媒体栏：媒体栏够宽时分镜图与视频并排（竖屏 440px、横屏 720px 起），否则上下排；
 * 配音卡在下方横跨整栏。
 */
export function ShotMediaGrid({
  aspectRatio,
  storyboard,
  video,
  audio,
}: {
  aspectRatio: "9:16" | "16:9";
  storyboard: ReactNode;
  video: ReactNode;
  audio?: ReactNode;
}) {
  const portrait = aspectRatio === "9:16";
  return (
    <div
      className={cn(
        "grid items-start gap-x-4 gap-y-5",
        portrait ? "@min-[440px]/shot-media:grid-cols-2" : "@min-[720px]/shot-media:grid-cols-2",
      )}
    >
      <div className="min-w-0">{storyboard}</div>
      <div className="flex min-w-0 flex-col">{video}</div>
      {audio ? (
        <div
          className={cn(
            "min-w-0",
            portrait ? "@min-[440px]/shot-media:col-span-2" : "@min-[720px]/shot-media:col-span-2",
          )}
        >
          {audio}
        </div>
      ) : null}
    </div>
  );
}
