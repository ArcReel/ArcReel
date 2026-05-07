import { memo } from "react";
import { assetColor } from "./asset-colors";
import type { AssetKind } from "@/types/reference-video";

export interface RefAvatarProps {
  kind: AssetKind;
  name: string;
  /** Resolved image URL (already cache-busted by caller) or null */
  imageUrl: string | null;
  size?: number;
}

/**
 * Square/round avatar for a referenced asset. character is rendered as a circle,
 * scene/prop as a rounded square — matches the v3 reference design's visual
 * shorthand for kind. Falls back to the first character of the name on a tinted
 * panel when no thumbnail is available.
 */
export const RefAvatar = memo(function RefAvatar({
  kind,
  name,
  imageUrl,
  size = 22,
}: RefAvatarProps) {
  const palette = assetColor(kind);
  const radius = kind === "character" ? size / 2 : Math.max(4, size * 0.25);
  const initial = name.slice(0, 1);

  if (imageUrl) {
    return (
      <img
        src={imageUrl}
        alt=""
        loading="lazy"
        aria-hidden="true"
        className={`shrink-0 object-cover ${palette.borderClass} border`}
        style={{ width: size, height: size, borderRadius: radius }}
      />
    );
  }

  return (
    <span
      aria-hidden="true"
      className={`grid shrink-0 place-items-center font-mono font-bold ${palette.bgClass} ${palette.textClass} ${palette.borderClass} border`}
      style={{
        width: size,
        height: size,
        borderRadius: radius,
        fontSize: Math.max(9, size * 0.42),
      }}
    >
      {initial}
    </span>
  );
});
