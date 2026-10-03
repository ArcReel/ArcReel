import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Loader2, Search } from "lucide-react";
import { cn } from "cn";
import { API } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import type { AssetType } from "@/types/asset";
import { ASSET_TYPE_ICON } from "./asset-type-icons";
import { AssetThumb } from "./AssetThumb";
import { LoadMoreSentinel } from "./LoadMoreSentinel";
import { useAssetPages } from "./useAssetPages";

interface Props {
  /** 当前画廊的类型，选择器只列这一类。 */
  type: AssetType;
  /** 项目里已有的同类资产名，对应条目不可选。 */
  existingNames: Set<string>;
  onClose: () => void;
  onImport: (assetIds: string[]) => void;
}

/**
 * 项目画廊里的「从资产库导入」：列出资产库里当前类型的资产，可搜索、滚动到底自动加载，显示匹配总数；多选后导入。
 */
export function AssetPickerModal({ type, existingNames, onClose, onImport }: Props) {
  const { t } = useTranslation("assets");
  const [q, setQ] = useState("");
  const debouncedQ = useDebouncedValue(q, 250).trim();
  const pages = useAssetPages({ type, q: debouncedQ });
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const Icon = ASSET_TYPE_ICON[type];
  const typeLabel = t(`type.${type}`);

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent size="xl">
        <DialogHeader>
          <DialogTitle>{t(`picker_title_${type}`)}</DialogTitle>
          <DialogDescription aria-live="polite">
            {pages.loading ? t("loading") : t("picker_match_count", { count: pages.total, type: typeLabel })}
          </DialogDescription>
          <InputGroup className="mt-2">
            <InputGroupAddon>
              <Search aria-hidden />
            </InputGroupAddon>
            <InputGroupInput
              type="search"
              aria-label={t("search_label")}
              placeholder={t("search_placeholder")}
              value={q}
              onChange={(event) => setQ(event.target.value)}
            />
          </InputGroup>
        </DialogHeader>
        <DialogBody>
          {pages.loading ? (
            <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
              <Loader2 aria-hidden className="size-4 animate-spin" />
              {t("loading")}
            </div>
          ) : pages.error ? (
            <div className="flex flex-col items-center gap-3 py-16 text-center">
              <p role="alert" className="text-sm text-destructive">
                {t("library_load_failed", { message: pages.error })}
              </p>
              <Button variant="outline" onClick={pages.retry}>
                {t("retry")}
              </Button>
            </div>
          ) : pages.items.length === 0 ? (
            <p className="py-16 text-center text-sm text-muted-foreground">
              {debouncedQ
                ? t("library_no_match", { type: typeLabel, query: debouncedQ })
                : t(`library_empty_${type}`)}
            </p>
          ) : (
            <div className="flex flex-col gap-3">
              <ul className="grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] gap-3">
                {pages.items.map((asset) => {
                  const inProject = existingNames.has(asset.name);
                  const isSelected = selected.has(asset.id);
                  return (
                    <li key={asset.id} className="flex min-w-0">
                      <button
                        type="button"
                        disabled={inProject}
                        aria-pressed={isSelected}
                        onClick={() => toggle(asset.id)}
                        className={cn(
                          "relative flex min-w-0 flex-1 flex-col gap-1.5 rounded-lg border bg-card p-2 text-left outline-none transition-colors duration-fast focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50",
                          isSelected ? "border-primary bg-primary/10" : "border-border hover:border-input",
                        )}
                      >
                        <AssetThumb
                          imageUrl={API.getGlobalAssetUrl(asset.image_path, asset.updated_at)}
                          alt=""
                          fallback={<Icon aria-hidden className="size-6" />}
                          className="rounded-md"
                        />
                        <span className="truncate text-sm font-medium">{asset.name}</span>
                        {inProject ? (
                          <span className="text-xs text-muted-foreground">{t("already_in_project")}</span>
                        ) : asset.description ? (
                          <span className="truncate text-xs text-muted-foreground">{asset.description}</span>
                        ) : null}
                        {isSelected && (
                          <span
                            aria-hidden
                            className="absolute top-3 right-3 flex size-5 items-center justify-center rounded-full bg-primary text-primary-foreground"
                          >
                            <Check className="size-3" />
                          </span>
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
              <LoadMoreSentinel
                hasMore={pages.hasMore}
                loading={pages.loadingMore}
                error={pages.moreError}
                itemCount={pages.items.length}
                onReach={pages.loadMore}
                onRetry={pages.retry}
              />
            </div>
          )}
        </DialogBody>
        <DialogFooter>
          <span className="mr-auto text-sm text-muted-foreground tabular-nums">
            {t("picker_selected", { count: selected.size })}
          </span>
          <DialogClose render={<Button variant="outline" />}>{t("cancel")}</DialogClose>
          <Button disabled={selected.size === 0} onClick={() => onImport(Array.from(selected))}>
            {t("import_count", { count: selected.size })}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
