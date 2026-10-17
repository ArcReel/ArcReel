import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { CatalogEntryHit, MediaType } from "@/types";
import { useCatalogEntrySearch } from "./useCatalogEntrySearch";

const MEDIA_TYPES: readonly MediaType[] = ["text", "image", "video", "audio"];

export type MediaFilter = MediaType | "all";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 面板打开时的媒体类型筛选；不给就从「全部」开始。 */
  mediaType?: MediaType;
  /** 选中一个条目。面板只把条目交回调用方并关闭，不保存任何东西。 */
  onSelect: (entry: CatalogEntryHit) => void;
  /** 面板标题（读屏可见）；不给用「从模型目录选择」。 */
  title?: string;
}

/**
 * 模型目录选择面板：按名称搜索、按媒体类型筛选，选中后把条目交给调用方。
 * 「关联其他模型」、获取模型对话框里的「对应模型」和 Agent 凭证的档位绑定共用它。
 */
export function CatalogEntryPicker({ open, onOpenChange, mediaType, onSelect, title }: Props) {
  const { t } = useTranslation("dashboard");
  const heading = title ?? t("catalog_picker_title");
  return (
    <CommandDialog open={open} onOpenChange={onOpenChange} title={heading} description={t("catalog_picker_description")}>
      <PickerBody
        initialFilter={mediaType ?? "all"}
        onSelect={(entry) => {
          onSelect(entry);
          onOpenChange(false);
        }}
      />
    </CommandDialog>
  );
}

function PickerBody({
  initialFilter,
  onSelect,
}: {
  initialFilter: MediaFilter;
  onSelect: (entry: CatalogEntryHit) => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<MediaFilter>(initialFilter);
  const search = useCatalogEntrySearch(query, filter === "all" ? undefined : filter);
  const settled = search.settled;
  return (
    <Command shouldFilter={false} loop>
      <CommandInput
        value={query}
        onValueChange={setQuery}
        placeholder={t("dashboard:catalog_picker_search")}
        aria-label={t("dashboard:catalog_picker_search")}
      />
      <CatalogMediaFilter value={filter} onChange={setFilter} />
      {search.error !== null ? (
        <div role="alert" className="flex flex-col items-start gap-2 px-2 py-4 text-sm">
          <p>{t("dashboard:catalog_picker_load_failed")}</p>
          <details className="text-xs text-muted-foreground">
            <summary>{t("dashboard:catalog_picker_error_details")}</summary>
            {search.error}
          </details>
          <Button variant="outline" size="sm" onClick={search.retry}>
            {t("common:retry")}
          </Button>
        </div>
      ) : (
        // 结果就绪前，上一次的结果仍显示但不可选，避免选中与当前搜索词或筛选不符的条目
        <CommandList label={t("dashboard:catalog_picker_list")} className="max-h-[50dvh]" aria-busy={!settled}>
          {search.entries !== null && (
            <>
              {settled && <CommandEmpty>{t("dashboard:catalog_picker_empty")}</CommandEmpty>}
              <CommandGroup heading={t("dashboard:catalog_picker_list")}>
                {search.entries.map((entry) => (
                  <CommandItem
                    key={`${entry.maker}/${entry.name}/${entry.media_type}`}
                    value={`${entry.maker}/${entry.name}/${entry.media_type}`}
                    disabled={!settled}
                    onSelect={() => onSelect(entry)}
                  >
                    <CatalogEntryLabel entry={entry} />
                  </CommandItem>
                ))}
              </CommandGroup>
            </>
          )}
        </CommandList>
      )}
    </Command>
  );
}

/** 目录条目在列表里的两行：显示名（已下架另标），厂商与媒体类型。 */
export function CatalogEntryLabel({ entry }: { entry: CatalogEntryHit }) {
  const { t } = useTranslation("dashboard");
  return (
    <span className="flex min-w-0 flex-1 flex-col gap-0.5">
      <span className="flex min-w-0 items-center gap-2">
        <span className="truncate">{entry.display_name}</span>
        {entry.discontinued && (
          <span className="shrink-0 text-xs text-destructive">{t("catalog_picker_discontinued")}</span>
        )}
      </span>
      <span className="truncate text-xs text-muted-foreground">
        {t(`catalog_maker_${entry.maker}`, { defaultValue: entry.maker })} · {t(`media_type_${entry.media_type}`)}
      </span>
    </span>
  );
}

/** 按媒体类型筛选：全部、文本、图片、视频、音频，总有一项选中。 */
export function CatalogMediaFilter({ value, onChange }: { value: MediaFilter; onChange: (next: MediaFilter) => void }) {
  const { t } = useTranslation("dashboard");
  return (
    <div className="px-1 py-2">
      <ToggleGroup
        aria-label={t("catalog_picker_media_filter")}
        variant="outline"
        size="sm"
        value={[value]}
        onValueChange={(next: string[]) => {
          // 再点已选中的一项不取消选择，筛选总有一项生效
          if (next[0]) onChange(next[0] as MediaFilter);
        }}
      >
        <ToggleGroupItem value="all">{t("catalog_picker_media_all")}</ToggleGroupItem>
        {MEDIA_TYPES.map((type) => (
          <ToggleGroupItem key={type} value={type}>
            {t(`media_type_${type}`)}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
    </div>
  );
}
