import { RefThumbnail } from "@/components/ui/RefThumbnail";
import type { Prop, Scene } from "@/types";
import type { AssetKind } from "@/types/reference-video";

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
  const all: Array<{ kind: AssetKind; name: string; asset: Scene | Prop | undefined }> = [
    ...sceneNames.map((name) => ({ kind: "scene" as const, name, asset: scenes[name] })),
    ...propNames.map((name) => ({ kind: "prop" as const, name, asset: props[name] })),
  ];

  if (all.length === 0) return null;

  const visible = all.slice(0, maxShow);
  const overflow = all.length - maxShow;

  return (
    <div className="flex items-center -space-x-2">
      {visible.map(({ kind, name, asset }) => (
        <RefThumbnail
          key={`${kind}-${name}`}
          kind={kind}
          name={name}
          asset={asset}
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
