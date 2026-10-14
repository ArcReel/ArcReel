import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
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
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import type { CatalogEntryHit, MediaType } from "@/types";
import { errMsg } from "@/utils/async";

const MEDIA_TYPES: readonly MediaType[] = ["text", "image", "video", "audio"];

type MediaFilter = MediaType | "all";

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

type SearchState =
  | { status: "ready"; key: string; entries: CatalogEntryHit[] }
  | { status: "error"; key: string; message: string };

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
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<SearchState | null>(null);
  const debouncedQuery = useDebouncedValue(query.trim(), 200);
  const requestKey = `${attempt}|${filter}|${debouncedQuery}`;

  useEffect(() => {
    const controller = new AbortController();
    API.searchCatalogEntries(
      { q: debouncedQuery, mediaType: filter === "all" ? undefined : filter },
      { signal: controller.signal },
    )
      .then((response) => {
        if (controller.signal.aborted) return;
        setResult({ status: "ready", key: requestKey, entries: response.entries });
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setResult({ status: "error", key: requestKey, message: errMsg(err) });
      });
    return () => controller.abort();
  }, [debouncedQuery, filter, attempt, requestKey]);

  const settled = result !== null && result.key === requestKey;
  const makerLabel = (maker: string) => t(`dashboard:catalog_maker_${maker}`, { defaultValue: maker });

  return (
    <Command shouldFilter={false} loop>
      <CommandInput
        value={query}
        onValueChange={setQuery}
        placeholder={t("dashboard:catalog_picker_search")}
        aria-label={t("dashboard:catalog_picker_search")}
      />
      <div className="px-1 py-2">
        <ToggleGroup
          aria-label={t("dashboard:catalog_picker_media_filter")}
          variant="outline"
          size="sm"
          value={[filter]}
          onValueChange={(next: string[]) => {
            // 再点已选中的一项不取消选择，筛选总有一项生效
            if (next[0]) setFilter(next[0] as MediaFilter);
          }}
        >
          <ToggleGroupItem value="all">{t("dashboard:catalog_picker_media_all")}</ToggleGroupItem>
          {MEDIA_TYPES.map((type) => (
            <ToggleGroupItem key={type} value={type}>
              {t(`dashboard:media_type_${type}`)}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>
      {result?.status === "error" && settled ? (
        <div role="alert" className="flex flex-col items-start gap-2 px-2 py-4 text-sm">
          <p>{t("dashboard:catalog_picker_load_failed")}</p>
          <details className="text-xs text-muted-foreground">
            <summary>{t("dashboard:catalog_picker_error_details")}</summary>
            {result.message}
          </details>
          <Button variant="outline" size="sm" onClick={() => setAttempt((n) => n + 1)}>
            {t("common:retry")}
          </Button>
        </div>
      ) : (
        <CommandList label={t("dashboard:catalog_picker_list")} className="max-h-[50dvh]">
          {result?.status === "ready" && (
            <>
              {settled && <CommandEmpty>{t("dashboard:catalog_picker_empty")}</CommandEmpty>}
              <CommandGroup heading={t("dashboard:catalog_picker_list")}>
                {result.entries.map((entry) => (
                  <CommandItem
                    key={`${entry.maker}/${entry.name}/${entry.media_type}`}
                    value={`${entry.maker}/${entry.name}/${entry.media_type}`}
                    onSelect={() => onSelect(entry)}
                  >
                    <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                      <span className="flex min-w-0 items-center gap-2">
                        <span className="truncate">{entry.display_name}</span>
                        {entry.discontinued && (
                          <span className="shrink-0 text-xs text-destructive">
                            {t("dashboard:catalog_picker_discontinued")}
                          </span>
                        )}
                      </span>
                      <span className="truncate text-xs text-muted-foreground">
                        {makerLabel(entry.maker)} · {t(`dashboard:media_type_${entry.media_type}`)}
                      </span>
                    </span>
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
