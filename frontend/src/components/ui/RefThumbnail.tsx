import { useRef, useState, type ComponentType, type RefObject } from "react";
import { MapPin, Puzzle, User } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { Popover } from "@/components/ui/Popover";
import { useProjectsStore } from "@/stores/projects-store";
import type { Character, Prop, Scene } from "@/types";
import { type AssetKind, SHEET_FIELD } from "@/types/reference-video";
import { colorForName } from "@/utils/color";

type Asset = Character | Scene | Prop;

interface KindMeta {
  shape: string;
  Icon: ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
  badgeClass: string;
  badgeKey:
    | "segment_refs_badge_character"
    | "segment_refs_badge_scene"
    | "segment_refs_badge_prop";
}

const KIND_META: Record<AssetKind, KindMeta> = {
  character: {
    shape: "rounded-full",
    Icon: User,
    badgeClass: "bg-indigo-800/60 text-indigo-300",
    badgeKey: "segment_refs_badge_character",
  },
  scene: {
    shape: "rounded",
    Icon: MapPin,
    badgeClass: "bg-amber-800/60 text-amber-300",
    badgeKey: "segment_refs_badge_scene",
  },
  prop: {
    shape: "rounded",
    Icon: Puzzle,
    badgeClass: "bg-emerald-800/60 text-emerald-300",
    badgeKey: "segment_refs_badge_prop",
  },
};

export function getSheetPath(
  kind: AssetKind,
  asset: Asset | undefined,
): string | undefined {
  if (!asset) return undefined;
  const value = (asset as unknown as Record<string, unknown>)[SHEET_FIELD[kind]];
  return typeof value === "string" ? value : undefined;
}

function RefPopover({
  kind,
  name,
  asset,
  projectName,
  anchorRef,
  sheetFp,
}: {
  kind: AssetKind;
  name: string;
  asset: Asset;
  projectName: string;
  anchorRef: RefObject<HTMLElement | null>;
  sheetFp: number | null;
}) {
  const { t } = useTranslation("dashboard");
  const meta = KIND_META[kind];
  const sheetPath = getSheetPath(kind, asset);
  const firstLine = asset.description?.split("\n")[0] ?? "";
  const { Icon } = meta;

  return (
    <Popover
      open
      anchorRef={anchorRef}
      align="center"
      sideOffset={6}
      width="w-[26rem]"
      layer="modal"
      className="pointer-events-none max-w-[calc(100vw-1.5rem)] rounded-lg border border-gray-700 p-2 shadow-xl"
    >
      <div className="flex items-start gap-2.5">
        {sheetPath ? (
          <img
            src={API.getFileUrl(projectName, sheetPath, sheetFp)}
            alt={name}
            className="h-[120px] w-[90px] shrink-0 rounded object-cover"
          />
        ) : (
          <div className="flex h-[120px] w-[90px] shrink-0 items-center justify-center rounded bg-gray-800">
            <Icon className="h-8 w-8 text-gray-600" aria-hidden />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <p className="truncate text-sm font-medium text-white">{name}</p>
            <span
              className={`shrink-0 rounded px-1 py-0.5 text-[10px] font-semibold ${meta.badgeClass}`}
            >
              {t(meta.badgeKey)}
            </span>
          </div>
          {firstLine && (
            <p className="mt-0.5 line-clamp-4 whitespace-normal break-words text-xs leading-relaxed text-gray-400">
              {firstLine}
            </p>
          )}
        </div>
      </div>
    </Popover>
  );
}

export function RefThumbnail({
  kind,
  name,
  asset,
  projectName,
}: {
  kind: AssetKind;
  name: string;
  asset: Asset | undefined;
  projectName: string;
}) {
  const sheetPath = getSheetPath(kind, asset);
  const sheetFp = useProjectsStore((s) =>
    sheetPath ? s.getAssetFingerprint(sheetPath) : null,
  );
  const [errorPath, setErrorPath] = useState<string | null>(null);
  const [hovered, setHovered] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const meta = KIND_META[kind];
  const showImage = !!sheetPath && errorPath !== sheetPath;

  return (
    <>
      <span
        ref={ref}
        className="relative inline-block"
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        {showImage ? (
          <img
            src={API.getFileUrl(projectName, sheetPath, sheetFp)}
            alt={name}
            className={`h-7 w-7 border-2 border-gray-900 object-cover ${meta.shape}`}
            onError={() => setErrorPath(sheetPath ?? null)}
          />
        ) : (
          <span
            className={`flex h-7 w-7 items-center justify-center border-2 border-gray-900 text-[10px] font-semibold text-white ${meta.shape} ${colorForName(name)}`}
          >
            {name.charAt(0)}
          </span>
        )}
      </span>
      {hovered && asset && (
        <RefPopover
          kind={kind}
          name={name}
          asset={asset}
          projectName={projectName}
          anchorRef={ref}
          sheetFp={sheetFp}
        />
      )}
    </>
  );
}
