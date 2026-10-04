import { useState, type ComponentType } from "react";
import { MapPin, Puzzle, User } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { cn } from "cn";
import { Badge } from "@/components/ui/badge";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import { useProjectsStore } from "@/stores/projects-store";
import type { Character, CharacterDerivative, Prop, Scene } from "@/types";
import { type AssetKind, SHEET_FIELD } from "@/types/reference-video";
import { formatReferenceName, referenceInitial, splitDerivativeReference } from "@/utils/reference-mentions";

type ThumbnailAssetKind = Exclude<AssetKind, "product">;
type Asset = Character | CharacterDerivative | Scene | Prop;

interface KindMeta {
  shape: string;
  Icon: ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
  badgeKey:
    | "segment_refs_badge_character"
    | "segment_refs_badge_scene"
    | "segment_refs_badge_prop";
}

const KIND_META: Record<ThumbnailAssetKind, KindMeta> = {
  character: { shape: "rounded-full", Icon: User, badgeKey: "segment_refs_badge_character" },
  scene: { shape: "rounded-sm", Icon: MapPin, badgeKey: "segment_refs_badge_scene" },
  prop: { shape: "rounded-sm", Icon: Puzzle, badgeKey: "segment_refs_badge_prop" },
};

type BadgeKey = KindMeta["badgeKey"] | "segment_refs_badge_character_derivative";

/** 引用名带衍生段时改用衍生徽标：浮层里显示的是这套外观，不是本体那条资产。 */
function badgeKeyFor(kind: ThumbnailAssetKind, name: string): BadgeKey {
  if (kind === "character" && splitDerivativeReference(name)[1]) {
    return "segment_refs_badge_character_derivative";
  }
  return KIND_META[kind].badgeKey;
}

export function getSheetPath(
  kind: ThumbnailAssetKind,
  asset: Asset | undefined,
): string | undefined {
  if (!asset) return undefined;
  const value = (asset as unknown as Record<string, unknown>)[SHEET_FIELD[kind]];
  return typeof value === "string" ? value : undefined;
}

/** 悬停时的资产预览：资产图、名称、类型与描述首行。 */
function RefPreview({
  kind,
  name,
  asset,
  projectName,
  sheetFp,
}: {
  kind: ThumbnailAssetKind;
  name: string;
  asset: Asset;
  projectName: string;
  sheetFp: number | null;
}) {
  const { t } = useTranslation("dashboard");
  const { Icon } = KIND_META[kind];
  const sheetPath = getSheetPath(kind, asset);
  const firstLine = asset.description?.split("\n")[0] ?? "";
  const displayName = formatReferenceName(name);

  return (
    <div className="flex items-start gap-2.5">
      {sheetPath ? (
        <img
          src={API.getFileUrl(projectName, sheetPath, sheetFp)}
          alt={displayName}
          className="h-30 w-22 shrink-0 rounded-sm object-cover"
        />
      ) : (
        <div className="flex h-30 w-22 shrink-0 items-center justify-center rounded-sm bg-muted">
          <Icon className="size-8 text-muted-foreground" aria-hidden />
        </div>
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <p className="truncate text-sm font-medium">{displayName}</p>
          <Badge variant="secondary">{t(badgeKeyFor(kind, name))}</Badge>
        </div>
        {firstLine && (
          <p className="mt-0.5 line-clamp-4 text-xs leading-relaxed break-words text-muted-foreground">{firstLine}</p>
        )}
      </div>
    </div>
  );
}

/**
 * 引用的缩略头像：有资产图时显示资产图，否则显示名称首字；悬停显示资产预览。
 * 常放在可点击的引用摘要里，自身不进入 Tab 顺序，名称由图片的替代文本或外层按钮提供。
 */
export function RefThumbnail({
  kind,
  name,
  asset,
  projectName,
}: {
  kind: ThumbnailAssetKind;
  name: string;
  asset: Asset | undefined;
  projectName: string;
}) {
  const sheetPath = getSheetPath(kind, asset);
  const sheetFp = useProjectsStore((s) =>
    sheetPath ? s.getAssetFingerprint(sheetPath) : null,
  );
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const meta = KIND_META[kind];
  const currentKey = sheetPath ? `${sheetPath}#${sheetFp ?? ""}` : null;
  const showImage = !!sheetPath && errorKey !== currentKey;

  const thumb = showImage ? (
    <img
      src={API.getFileUrl(projectName, sheetPath, sheetFp)}
      alt={formatReferenceName(name)}
      className={cn("size-7 border-2 border-background object-cover", meta.shape)}
      onError={() => setErrorKey(currentKey)}
    />
  ) : (
    <span
      className={cn(
        "flex size-7 items-center justify-center border-2 border-background bg-secondary text-xs font-medium text-subtle-foreground",
        meta.shape,
      )}
    >
      {referenceInitial(name)}
    </span>
  );

  if (!asset) return <span className="relative inline-block">{thumb}</span>;

  return (
    <HoverCard>
      <HoverCardTrigger render={<span className="relative inline-block" />} delay={300}>
        {thumb}
      </HoverCardTrigger>
      <HoverCardContent className="w-104 max-w-[calc(100dvw-1.5rem)]">
        <RefPreview kind={kind} name={name} asset={asset} projectName={projectName} sheetFp={sheetFp} />
      </HoverCardContent>
    </HoverCard>
  );
}
