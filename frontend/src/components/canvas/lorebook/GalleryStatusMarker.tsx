import { useTranslation } from "react-i18next";
import { cn } from "cn";
import type { GalleryMarker } from "./gallery-model";

const TONE: Record<Exclude<GalleryMarker, "current">, string> = {
  generating: "text-primary",
  "no-description": "text-warn",
  missing: "text-subtle-foreground",
  stale: "text-warn",
};

/** 浏览卡图片左上角的状态标记：生成中用呼吸点，资产图已是最新时不显示。 */
export function GalleryStatusMarker({ marker, className }: { marker: GalleryMarker; className?: string }) {
  const { t } = useTranslation("assets");
  if (marker === "current") return null;
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center gap-1.5 rounded-md bg-background px-1.5 text-xs font-medium",
        TONE[marker],
        className,
      )}
    >
      <span aria-hidden className={cn("size-1.5 rounded-full bg-current", marker === "generating" && "animate-breath")} />
      {t(`gallery_marker.${marker}`)}
    </span>
  );
}
