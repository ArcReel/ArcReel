import { useEffect, useRef, useState, type RefObject } from "react";
import { MapPin, Puzzle } from "lucide-react";
import { API } from "@/api";
import { Popover } from "@/components/ui/Popover";
import { useProjectsStore } from "@/stores/projects-store";
import type { Scene, Prop } from "@/types";

import { colorForName } from "@/utils/color";

// ---------------------------------------------------------------------------
// AssetPopover — shows scene/prop detail on hover
// ---------------------------------------------------------------------------

type AssetKind = "scene" | "prop";

function getSheetPath(kind: AssetKind, asset: Scene | Prop): string | undefined {
  return kind === "scene"
    ? (asset as Scene).scene_sheet
    : (asset as Prop).prop_sheet;
}

function AssetPopover({
  name,
  kind,
  asset,
  projectName,
  anchorRef,
  sheetFp,
}: {
  name: string;
  kind: AssetKind;
  asset: Scene | Prop;
  projectName: string;
  anchorRef: RefObject<HTMLElement | null>;
  sheetFp: number | null;
}) {
  const firstLine = asset.description?.split("\n")[0] ?? "";
  const typeLabel = kind === "scene" ? "场景" : "道具";
  const typeBadgeClass =
    kind === "scene"
      ? "bg-amber-800/60 text-amber-300"
      : "bg-emerald-800/60 text-emerald-300";
  const sheetPath = getSheetPath(kind, asset);

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
            {kind === "scene" ? (
              <MapPin className="h-8 w-8 text-gray-600" />
            ) : (
              <Puzzle className="h-8 w-8 text-gray-600" />
            )}
          </div>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <p className="truncate text-sm font-medium text-white">{name}</p>
            <span
              className={`shrink-0 rounded px-1 py-0.5 text-[10px] font-semibold ${typeBadgeClass}`}
            >
              {typeLabel}
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

// ---------------------------------------------------------------------------
// SingleAsset — one rounded-square thumbnail with hover popover
// ---------------------------------------------------------------------------

function SingleAsset({
  name,
  kind,
  asset,
  projectName,
}: {
  name: string;
  kind: AssetKind;
  asset: Scene | Prop | undefined;
  projectName: string;
}) {
  const [imgError, setImgError] = useState(false);
  const [hovered, setHovered] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);

  const sheetPath = asset ? getSheetPath(kind, asset) : undefined;
  const sheetFp = useProjectsStore(
    (s) => sheetPath ? s.getAssetFingerprint(sheetPath) : null,
  );
  const showImage = sheetPath && !imgError;

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 图片源变更时重置错误态，确保新 URL 正常加载
    if (imgError) setImgError(false);
  }, [sheetFp, sheetPath]); // eslint-disable-line react-hooks/exhaustive-deps

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
            className="h-7 w-7 rounded border-2 border-gray-900 object-cover"
            onError={() => setImgError(true)}
          />
        ) : (
          <span
            className={`flex h-7 w-7 items-center justify-center rounded border-2 border-gray-900 text-[10px] font-semibold text-white ${colorForName(name)}`}
          >
            {name.charAt(0)}
          </span>
        )}
      </span>
      {hovered && asset && (
        <AssetPopover
          name={name}
          kind={kind}
          asset={asset}
          projectName={projectName}
          anchorRef={ref}
          sheetFp={sheetFp}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// ClueStack — renders a combined stack of scene + prop thumbnails
// ---------------------------------------------------------------------------

interface ClueStackProps {
  sceneNames: string[];
  propNames: string[];
  scenes: Record<string, Scene>;
  props: Record<string, Prop>;
  projectName: string;
  maxShow?: number;
}

export function ClueStack({
  sceneNames,
  propNames,
  scenes,
  props,
  projectName,
  maxShow = 4,
}: ClueStackProps) {
  const allNames = [
    ...sceneNames.map((n) => ({ name: n, kind: "scene" as AssetKind })),
    ...propNames.map((n) => ({ name: n, kind: "prop" as AssetKind })),
  ];

  if (allNames.length === 0) return null;

  const visible = allNames.slice(0, maxShow);
  const overflow = allNames.length - maxShow;

  return (
    <div className="flex -space-x-2">
      {visible.map(({ name, kind }) => (
        <SingleAsset
          key={`${kind}-${name}`}
          name={name}
          kind={kind}
          asset={kind === "scene" ? scenes[name] : props[name]}
          projectName={projectName}
        />
      ))}
      {overflow > 0 && (
        <span className="flex h-7 w-7 items-center justify-center rounded border-2 border-gray-900 bg-gray-700 text-[10px] font-semibold text-gray-300">
          +{overflow}
        </span>
      )}
    </div>
  );
}
