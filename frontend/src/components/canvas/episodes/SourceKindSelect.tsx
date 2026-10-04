import { useTranslation } from "react-i18next";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { SourceKind } from "@/types/episodes-view";

const SOURCE_KINDS: readonly SourceKind[] = ["novel", "screenplay"];

interface SourceKindSelectProps {
  value: SourceKind;
  onChange: (value: SourceKind) => void;
  /** 无障碍名称，写明是哪份原文的类型。 */
  label: string;
  disabled?: boolean;
}

/** 源文件类型下拉：小说 / 剧本。只在剧情演绎项目里出现；选项下方说明两种类型的区别。 */
export function SourceKindSelect({ value, onChange, label, disabled }: SourceKindSelectProps) {
  const { t } = useTranslation("dashboard");
  const items = SOURCE_KINDS.map((kind) => ({
    value: kind,
    label: t(kind === "screenplay" ? "source_kind_screenplay" : "source_kind_novel"),
  }));
  return (
    <Select
      items={items}
      value={value}
      onValueChange={(next) => {
        if (next !== null) onChange(next);
      }}
      disabled={disabled}
    >
      <SelectTrigger size="sm" aria-label={label} className="shrink-0">
        <SelectValue />
      </SelectTrigger>
      <SelectContent alignItemWithTrigger={false} align="start" className="w-auto max-w-xs">
        {items.map((item) => (
          <SelectItem key={item.value} value={item.value}>
            <span className="flex flex-col gap-0.5 whitespace-normal">
              <span>{item.label}</span>
              <span className="text-xs text-muted-foreground">
                {t(item.value === "screenplay" ? "source_kind_screenplay_desc" : "source_kind_novel_desc")}
              </span>
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
