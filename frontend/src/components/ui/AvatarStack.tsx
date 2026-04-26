import { RefThumbnail } from "@/components/ui/RefThumbnail";
import type { Character } from "@/types";

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
    <div className="flex items-center -space-x-2">
      {visible.map((name) => (
        <RefThumbnail
          key={name}
          kind="character"
          name={name}
          asset={characters[name]}
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
