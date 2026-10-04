import { useTranslation } from "react-i18next";
import { Library, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import type { AssetSheetType } from "@/types";
import { ASSET_TYPE_ICON } from "./AssetBrowseCard";

/** 画廊还没有资产时的空状态；只读展示时不提供添加入口。 */
export function GalleryEmptyState({
  assetType,
  onAdd,
  onPickFromLibrary,
}: {
  assetType: AssetSheetType;
  onAdd?: () => void;
  onPickFromLibrary?: () => void;
}) {
  const { t } = useTranslation("assets");
  const Icon = ASSET_TYPE_ICON[assetType];
  return (
    <Empty>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <Icon aria-hidden />
        </EmptyMedia>
        <EmptyTitle>{t(`gallery_empty_title.${assetType}`)}</EmptyTitle>
        {onAdd && <EmptyDescription>{t(`gallery_empty_hint.${assetType}`)}</EmptyDescription>}
      </EmptyHeader>
      {onAdd && (
        <EmptyContent className="flex-row justify-center">
          <Button onClick={onAdd}>
            <Plus aria-hidden data-icon="inline-start" />
            {t(`gallery_add.${assetType}`)}
          </Button>
          {onPickFromLibrary && (
            <Button variant="outline" onClick={onPickFromLibrary}>
              <Library aria-hidden data-icon="inline-start" />
              {t("from_library")}
            </Button>
          )}
        </EmptyContent>
      )}
    </Empty>
  );
}
