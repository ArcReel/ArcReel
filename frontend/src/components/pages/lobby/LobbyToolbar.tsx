import { useTranslation } from "react-i18next";
import { Search } from "lucide-react";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { LOBBY_FILTERS, type LobbyFilter } from "./lobby-projects";

interface LobbyToolbarProps {
  filter: LobbyFilter;
  onFilterChange: (filter: LobbyFilter) => void;
  counts: Record<LobbyFilter, number>;
  /** 搜索词与改写：桌面在顶栏搜索，移动端在工具栏单独一行。 */
  query: string;
  onQueryChange: (query: string) => void;
}

/**
 * 吸顶在外壳主体顶端的工具栏：左边是带计数的筛选，右边说明排序方式。排序固定按最近活动，不提供切换。
 * 底色不透明，滚过的卡片不会透出来。
 */
export function LobbyToolbar({ filter, onFilterChange, counts, query, onQueryChange }: LobbyToolbarProps) {
  const { t } = useTranslation("dashboard");
  return (
    <div className="sticky top-0 z-sticky flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border bg-background py-2">
      {/* 移动端顶栏放不下搜索：单独一行，占满宽度 */}
      <InputGroup className="w-full @md/page:hidden">
        <InputGroupAddon>
          <Search aria-hidden />
        </InputGroupAddon>
        <InputGroupInput
          type="search"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          aria-label={t("lobby_search_label")}
          placeholder={t("lobby_search_placeholder")}
          autoComplete="off"
          spellCheck={false}
        />
      </InputGroup>
      <div className="relative max-w-full overflow-x-auto scroll-fade-x">
      <ToggleGroup
        aria-label={t("lobby_filter_label")}
        size="sm"
        value={[filter]}
        onValueChange={(next: string[]) => {
          // 再次点击当前筛选不会取消选中。
          const picked = next.find((value): value is LobbyFilter => (LOBBY_FILTERS as readonly string[]).includes(value));
          if (picked) onFilterChange(picked);
        }}
      >
        {LOBBY_FILTERS.map((key) => (
          <ToggleGroupItem key={key} value={key}>
            {t(`lobby_filter_${key}`)}
            <span className="text-muted-foreground tabular-nums">{counts[key]}</span>
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
      </div>
      <span className="ml-auto shrink-0 text-xs text-muted-foreground">{t("lobby_sort_recent")}</span>
    </div>
  );
}
