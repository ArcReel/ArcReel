import { useEffect, useRef, useState, type RefObject } from "react";
import { Puzzle } from "lucide-react";
import { API } from "@/api";
import { Popover } from "@/components/ui/Popover";
import { useProjectsStore } from "@/stores/projects-store";
import type { Clue } from "@/types";

import { colorForName } from "@/utils/color";

// ---------------------------------------------------------------------------
// CluePopover — shows clue detail on hover
// ---------------------------------------------------------------------------

function CluePopover({
  name,
  clue,
  projectName,
  anchorRef,
  sheetFp,
}: {
  name: string;
  clue: Clue;
  projectName: string;
  anchorRef: RefObject<HTMLElement | null>;
  sheetFp: number | null;
}) {

  const firstLine = clue.description?.split("\n")[0] ?? "";
  const typeLabel = clue.type === "location" ? "场景" : "道具";
  const typeBadgeClass =
    clue.type === "location"
      ? "bg-amber-800/60 text-amber-300"
      : "bg-emerald-800/60 text-emerald-300";

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
        {clue.clue_sheet ? (
          <img
            src={API.getFileUrl(projectName, clue.clue_sheet, sheetFp)}
            alt={name}
            className="h-[120px] w-[90px] shrink-0 rounded object-cover"
          />
        ) : (
          <div className="flex h-[120px] w-[90px] shrink-0 items-center justify-center rounded bg-gray-800">
            <Puzzle className="h-8 w-8 text-gray-600" />
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
// SingleClue — one rounded-square thumbnail with hover popover
// ---------------------------------------------------------------------------

function SingleClue({
  name,
  clue,
  projectName,
}: {
  name: string;
  clue: Clue | undefined;
  projectName: string;
}) {
  const [imgError, setImgError] = useState(false);
  const [hovered, setHovered] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);

  const sheetPath = clue?.clue_sheet;
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
      {hovered && clue && (
        <CluePopover
          name={name}
          clue={clue}
          projectName={projectName}
          anchorRef={ref}
          sheetFp={sheetFp}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// ClueStack
// ---------------------------------------------------------------------------

interface ClueStackProps {
  names: string[];
  clues: Record<string, Clue>;
  projectName: string;
  maxShow?: number;
}

export function ClueStack({
  names,
  clues,
  projectName,
  maxShow = 4,
}: ClueStackProps) {
  if (names.length === 0) return null;

  const visible = names.slice(0, maxShow);
  const overflow = names.length - maxShow;

  return (
    <div className="flex -space-x-2">
      {visible.map((name) => (
        <SingleClue
          key={name}
          name={name}
          clue={clues[name]}
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
