import { RefThumbnail } from "./RefThumbnail";
import type { Character } from "@/types";
import { resolveCharacterForm } from "@/utils/reference-mentions";

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
        // Names may use `本体/衍生` (base/derivative): resolve that appearance's own entry so the avatar and popover use its asset image and change description.
        <RefThumbnail
          key={name}
          kind="character"
          name={name}
          asset={resolveCharacterForm(characters, name)?.asset}
          projectName={projectName}
        />
      ))}
      {overflow > 0 && (
        <span className="flex h-7 w-7 items-center justify-center rounded-full border-2 border-background bg-sidebar text-xs font-semibold text-subtle-foreground">
          +{overflow}
        </span>
      )}
    </div>
  );
}
