import { useEffect, useRef, useState, type RefObject } from "react";
import { User } from "lucide-react";
import { API } from "@/api";
import { Popover } from "@/components/ui/Popover";
import { useProjectsStore } from "@/stores/projects-store";
import type { Character } from "@/types";

// ---------------------------------------------------------------------------
// Color assignment for fallback avatars (deterministic based on name)
// ---------------------------------------------------------------------------

const FALLBACK_COLORS = [
  "bg-rose-700",
  "bg-sky-700",
  "bg-emerald-700",
  "bg-amber-700",
  "bg-violet-700",
  "bg-teal-700",
  "bg-pink-700",
  "bg-indigo-700",
];

function colorForName(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) | 0;
  }
  return FALLBACK_COLORS[Math.abs(hash) % FALLBACK_COLORS.length];
}

// ---------------------------------------------------------------------------
// AvatarPopover — shows character detail on hover
// ---------------------------------------------------------------------------

function AvatarPopover({
  name,
  character,
  projectName,
  anchorRef,
}: {
  name: string;
  character: Character;
  projectName: string;
  anchorRef: RefObject<HTMLElement | null>;
}) {
  const sheetFp = useProjectsStore(
    (s) => character.character_sheet ? s.getAssetFingerprint(character.character_sheet) : null,
  );

  const firstLine = character.description?.split("\n")[0] ?? "";

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
        {character.character_sheet ? (
          <img
            src={API.getFileUrl(projectName, character.character_sheet, sheetFp)}
            alt={name}
            className="h-[120px] w-[90px] shrink-0 rounded object-cover"
          />
        ) : (
          <div className="flex h-[120px] w-[90px] shrink-0 items-center justify-center rounded bg-gray-800">
            <User className="h-8 w-8 text-gray-600" />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-white">{name}</p>
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
// SingleAvatar — one circular thumbnail with hover popover
// ---------------------------------------------------------------------------

function SingleAvatar({
  name,
  character,
  projectName,
}: {
  name: string;
  character: Character | undefined;
  projectName: string;
}) {
  const [imgError, setImgError] = useState(false);
  const [hovered, setHovered] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);

  const sheetPath = character?.character_sheet;
  const sheetFp = useProjectsStore(
    (s) => sheetPath ? s.getAssetFingerprint(sheetPath) : null,
  );
  const showImage = sheetPath && !imgError;

  useEffect(() => {
    setImgError(false);
  }, [sheetFp, sheetPath]);

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
            className="h-7 w-7 rounded-full border-2 border-gray-900 object-cover"
            onError={() => setImgError(true)}
          />
        ) : (
          <span
            className={`flex h-7 w-7 items-center justify-center rounded-full border-2 border-gray-900 text-[10px] font-semibold text-white ${colorForName(name)}`}
          >
            {name.charAt(0)}
          </span>
        )}
      </span>
      {hovered && character && (
        <AvatarPopover
          name={name}
          character={character}
          projectName={projectName}
          anchorRef={ref}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// AvatarStack
// ---------------------------------------------------------------------------

interface AvatarStackProps {
  names: string[];
  characters: Record<string, Character>;
  projectName: string;
  maxShow?: number;
}

export function AvatarStack({
  names,
  characters,
  projectName,
  maxShow = 4,
}: AvatarStackProps) {
  if (names.length === 0) return null;

  const visible = names.slice(0, maxShow);
  const overflow = names.length - maxShow;

  return (
    <div className="flex -space-x-2">
      {visible.map((name) => (
        <SingleAvatar
          key={name}
          name={name}
          character={characters[name]}
          projectName={projectName}
        />
      ))}
      {overflow > 0 && (
        <span className="flex h-7 w-7 items-center justify-center rounded-full border-2 border-gray-900 bg-gray-700 text-[10px] font-semibold text-gray-300">
          +{overflow}
        </span>
      )}
    </div>
  );
}
