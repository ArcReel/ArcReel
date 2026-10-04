import { useId } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

interface SourceTextReadonlyProps {
  /** 条目的对应原文；手动新增的条目没有对应原文。 */
  text: string | undefined;
  className?: string;
}

/**
 * 分镜 / 视频单元的对应原文，只读展示供对照来源。
 * 时间线不提供编辑：重新锚定由 Agent 写入，服务端校验它是源文的逐字片段。
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
