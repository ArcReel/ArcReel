import { RefThumbnail } from "@/components/ui/RefThumbnail";
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
        // 名字可以是 `本体/衍生`：取该形态自己的条目，头像与浮层因此是这套外观的资产图与变化描述。
        <RefThumbnail
          key={name}
          kind="character"
          name={name}
          asset={resolveCharacterForm(characters, name)?.asset}
          projectName={projectName}
        />
      ))}
      {overflow > 0 && (
        <span className="flex h-7 w-7 items-center justify-center rounded-full border-2 border-bg bg-bg-grad-b text-[10px] font-semibold text-text-2">
          +{overflow}
        </span>
      )}
    </div>
  );
}
