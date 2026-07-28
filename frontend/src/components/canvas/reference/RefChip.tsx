import { forwardRef, type CSSProperties, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { X } from "lucide-react";
import { assetColor } from "./asset-colors";
import { RefAvatar } from "./RefAvatar";
import type { AssetKind } from "@/types/reference-video";

export interface RefChipProps {
  kind: AssetKind;
  name: string;
  imageUrl: string | null;
  /** Optional `[图N]` index prefix; rendered to the left of the chip when set. */
  index?: number;
  /** Show an X delete button on the right. */
  removable?: boolean;
  onRemove?: () => void;
  /** Drag attributes/listeners passed in by the parent's SortableContext wrapper. */
  dragAttributes?: Record<string, unknown>;
  dragListeners?: Record<string, unknown>;
  isDragging?: boolean;
  style?: CSSProperties;
  /** Optional extra trailing content (e.g. lightbox button). */
  trailing?: ReactNode;
}

/**
 * Horizontal pill chip representing a referenced asset (character/scene/prop).
 * Visual aligned with the v3 reference design. Pure display — drag/sortable
 * wiring is provided by the parent.
 */
export const RefChip = forwardRef<HTMLDivElement, RefChipProps>(function RefChip(
  {
    kind,
    name,
    imageUrl,
    index,
    removable,
    onRemove,
    dragAttributes,
    dragListeners,
    isDragging,
    style,
    trailing,
  },
  ref,
) {
  const { t } = useTranslation("dashboard");
  const palette = assetColor(kind);
  const kindLabel = t(`reference_picker_group_${kind}`);

  return (
    <span className="inline-flex items-center gap-1.5">
      {typeof index === "number" && (
        <span
          aria-hidden="true"
          className="font-mono text-[10px] tabular-nums text-gray-500"
          translate="no"
        >
          [{t("reference_strip_image_token", { n: index + 1 })}]
        </span>
      )}
      <div
        ref={ref}
        style={style}
        {...dragAttributes}
        {...dragListeners}
        className={`group/chip inline-flex max-w-full cursor-grab touch-none items-center gap-1.5 rounded-full border py-0.5 pl-1 pr-2 text-xs font-medium transition-colors ${palette.bgClass} ${palette.borderClass} ${palette.textClass} ${
          isDragging ? "opacity-60" : ""
        } active:cursor-grabbing focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400`}
      >
        <RefAvatar kind={kind} name={name} imageUrl={imageUrl} size={18} />
        <span className="truncate text-gray-200" title={name}>
          {name}
        </span>
        <span
          aria-hidden="true"
          className={`-ml-0.5 font-mono text-[9px] font-bold uppercase tracking-wider ${palette.textClass}`}
        >
          {kindLabel}
        </span>
        {trailing}
        {removable && (
          <button
            type="button"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onRemove?.();
            }}
            aria-label={t("reference_panel_remove_aria", { name })}
            className="grid place-items-center rounded text-gray-400 transition-colors hover:text-red-400 focus-visible:text-red-400 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-red-400"
          >
            <X className="h-3 w-3" aria-hidden="true" />
          </button>
        )}
      </div>
    </span>
  );
});
