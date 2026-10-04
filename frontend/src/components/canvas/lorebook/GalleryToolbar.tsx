import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Library, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { AssetSheetType } from "@/types";
import type { GalleryFilter } from "./gallery-model";

const FILTERS: GalleryFilter[] = ["all", "pending", "stale"];

/**
 * 画廊工具栏：标题与数量、按资产图状态筛选、批量生成，以及「从资产库选择」与新增入口。
 * 吸在画廊滚动区顶部；画布变窄时控件换行。
 */
export function GalleryToolbar({
  assetType,
  title,
  count,
  filter,
  onFilterChange,
  filterCounts,
  onAdd,
  onPickFromLibrary,
  children,
}: {
  assetType: AssetSheetType;
  title: string;
  count: number;
  filter: GalleryFilter;
  onFilterChange: (filter: GalleryFilter) => void;
  /** 各筛选项命中的数量，「全部」即 `count`。 */
  filterCounts: Record<Exclude<GalleryFilter, "all">, number>;
  /** 未提供时隐藏「新增」入口（如只读展示的引导演示项目）。 */
  onAdd?: () => void;
  /** 未提供时隐藏「从资产库选择」入口（如不入全局库的资产类型）。 */
  onPickFromLibrary?: () => void;
  /** 批量生成入口。 */
  children?: ReactNode;
}) {
  const { t } = useTranslation("assets");
  const counts: Record<GalleryFilter, number> = { all: count, ...filterCounts };
  return (
    <div className="sticky top-0 z-sticky flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 border-b border-border bg-background px-5 py-2.5">
      <h2 className="flex items-baseline gap-2 text-base font-semibold">
        {title}
        <span className="text-sm font-normal text-muted-foreground tabular-nums">{count}</span>
      </h2>
      <ToggleGroup
        aria-label={t("sheet_filter_label")}
        variant="outline"
        size="sm"
        value={[filter]}
        onValueChange={(next: string[]) => {
          // 再点已选中的一项不取消选择，筛选总有一项生效
          if (next[0]) onFilterChange(next[0] as GalleryFilter);
        }}
      >
        {FILTERS.map((value) => (
          <ToggleGroupItem key={value} value={value}>
            {t(`sheet_filter_${value}`)}
            {value !== "all" && <span className="text-muted-foreground tabular-nums">{counts[value]}</span>}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
      <div className="flex flex-1 flex-wrap items-center justify-end gap-2">
        {children}
        {onPickFromLibrary && (
          <Button variant="outline" size="sm" onClick={onPickFromLibrary}>
            <Library aria-hidden data-icon="inline-start" />
            {t("from_library")}
          </Button>
        )}
        {onAdd && (
          <Button size="sm" onClick={onAdd}>
            <Plus aria-hidden data-icon="inline-start" />
            {t(`gallery_add.${assetType}`)}
          </Button>
        )}
      </div>
    </div>
  );
}
