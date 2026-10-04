import { useId, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";

/**
 * 分镜详情的栏位：页头与提示条在上，下面是「编辑栏 + 媒体栏」两栏，各自滚动。
 * 不做拖拽调宽，按分镜详情自身的宽度切换：窄于 860px 时编辑栏占满、媒体栏 300px；
 * 否则编辑栏 360–560px，多出的宽度全部给媒体栏。
 */
export function ShotDetailLayout({
  header,
  banner,
  main,
  media,
}: {
  header: ReactNode;
  /** 页头下方横跨两栏的提示（如未保存修改）。 */
  banner?: ReactNode;
  main: ReactNode;
  media: ReactNode;
}) {
  return (
    <div className="flex min-h-0 min-w-0 flex-col">
      {header}
      {banner}
      <div className="@container/shot-detail flex min-h-0 flex-1 flex-col">
        <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_300px] grid-rows-[minmax(0,1fr)] @min-[860px]/shot-detail:grid-cols-[minmax(360px,560px)_minmax(0,1fr)]">
          <div className="relative min-h-0 overflow-y-auto [scrollbar-gutter:stable]">
            <div className="flex flex-col gap-5 px-5 pt-4 pb-8">{main}</div>
          </div>
          <div className="@container/shot-media relative min-h-0 overflow-y-auto border-l border-border/50 px-4 pt-4 pb-8">
            {media}
          </div>
        </div>
      </div>
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
 */
export function ShotSection({
  title,
  icon,
  htmlFor,
  actions,
  children,
}: {
  title: string;
  icon?: ReactNode;
  htmlFor?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  const titleId = useId();
  const heading = htmlFor ? (
    <label id={titleId} htmlFor={htmlFor} className="text-xs font-medium text-muted-foreground">
      {title}
    </label>
  ) : (
    <h3 id={titleId} className="text-xs font-medium text-muted-foreground">
      {title}
    </h3>
  );
  return (
    <section aria-labelledby={titleId} className="flex min-w-0 flex-col gap-2">
      <div className="flex min-h-7 items-center gap-1.5">
        {icon ? <span className="flex text-muted-foreground">{icon}</span> : null}
        {heading}
        {actions ? <div className="ml-auto flex items-center gap-1">{actions}</div> : null}
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
