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
  /** Initial media-type filters; omission starts at All. */
  mediaType?: MediaType;
  /** Select entries: return them to callers and close panels without saving anything. */
  onSelect: (entry: CatalogEntryHit) => void;
  /** Screen-reader-visible panel titles; defaults to Choose from model catalog. */
  title?: string;
}

/**
 * Model-catalog selection panels: search names/filter media types, then return selected entries.
 * Shared by Associate another model, Fetch models Corresponding model, and Agent-credential tier binding.
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
        // Previous results remain visible but unselectable until new results arrive, preventing selection against stale searches/filters.
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

/** Two catalog-entry lines: display names (mark retired separately), then vendors/media types. */
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

/** Media filters: All/text/image/video/audio, always with one selected. */
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
          // Clicking selected options never deselects them; one filter always applies.
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
