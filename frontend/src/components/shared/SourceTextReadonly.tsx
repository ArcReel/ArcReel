import { useId } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

interface SourceTextReadonlyProps {
  /** Source text corresponding to the entry; manually added entries have none. */
  text: string | undefined;
  className?: string;
}

/**
 * Read-only corresponding source text for storyboard / video units, allowing comparison against the source.
 * The timeline offers no editing: the Agent writes reanchoring and the server validates a verbatim source excerpt.
 */
export function SourceTextReadonly({ text, className }: SourceTextReadonlyProps) {
  const { t } = useTranslation("dashboard");
  const labelId = useId();
  const content = text ?? "";
  const hasContent = content.trim().length > 0;
  return (
    <section aria-labelledby={labelId} className={className}>
      <div className="mb-1.5 flex flex-wrap items-baseline gap-x-2">
        <h4 id={labelId} className="text-xs font-medium text-muted-foreground">
          {t("detail_section_source_text")}
        </h4>
        <span className="text-xs text-muted-foreground">{t("detail_source_text_readonly_hint")}</span>
      </div>
      <p
        className={cn(
          "border-l-2 border-border pl-3 text-xs leading-relaxed whitespace-pre-wrap text-muted-foreground",
          !hasContent && "italic",
        )}
      >
        {hasContent ? content : t("detail_source_text_empty")}
      </p>
    </section>
  );
}
