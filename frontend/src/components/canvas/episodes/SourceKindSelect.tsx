import { useTranslation } from "react-i18next";

import { INPUT_CLS } from "@/components/ui/darkroom-tokens";
import type { SourceKind } from "@/types/episodes-view";

const SOURCE_KINDS: readonly SourceKind[] = ["novel", "screenplay"];

interface SourceKindSelectProps {
  value: SourceKind;
  onChange: (value: SourceKind) => void;
  /** 无障碍名称，写明是哪份原文的类型。 */
  label: string;
  disabled?: boolean;
}

/** 源文件类型下拉：小说 / 剧本。只在剧情演绎项目里出现。 */
export function SourceKindSelect({ value, onChange, label, disabled }: SourceKindSelectProps) {
  const { t } = useTranslation("dashboard");
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value as SourceKind)}
      aria-label={label}
      title={t(value === "screenplay" ? "source_kind_screenplay_desc" : "source_kind_novel_desc")}
      className={`${INPUT_CLS} !w-auto shrink-0 !py-1 !pl-2 !pr-6 !text-[11.5px]`}
      disabled={disabled}
    >
      {SOURCE_KINDS.map((kind) => (
        <option key={kind} value={kind}>
          {t(kind === "screenplay" ? "source_kind_screenplay" : "source_kind_novel")}
        </option>
      ))}
    </select>
  );
}
