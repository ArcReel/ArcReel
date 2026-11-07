import { useId, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";

/**
 * Shot-detail regions: header, independently scrolling editor/media columns, and unsaved notice at the bottom.
 * No drag resizing; respond to detail width. Below 480px (e.g. compact demo workbench with Agent panel
 * not covering canvas), stack one column with overall scrolling; 480–860px uses remaining editor width and 300px media;
 * wider layouts give editor 360–560px and all remaining width to media.
 */
export function ShotDetailLayout({
  header,
  footer,
  main,
  media,
}: {
  header: ReactNode;
  /** Full-width unsaved notice below both columns; appearing does not move fields being edited. */
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

/** One editor content group, separated from other groups by dividers. */
export function ShotGroup({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-col gap-4 border-t border-border/50 pt-5 first:border-t-0 first:pt-0">{children}</div>
  );
}

/**
 * Editor section: small title/content. With `htmlFor`, title labels the matching input;
 * otherwise it is a level-three heading. `actions` goes on the title row right.
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

/** Initially collapsed corresponding-source/references section. */
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
 * Media column: storyboard/video side by side when wide enough (portrait 440px, landscape 720px), otherwise stacked;
 * voiceover card spans the full column below.
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
