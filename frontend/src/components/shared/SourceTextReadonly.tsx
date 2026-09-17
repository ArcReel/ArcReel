import { useId } from "react";
import { useTranslation } from "react-i18next";

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
  const content = text?.trim() ?? "";
  return (
    <section aria-labelledby={labelId} className={className}>
      <div className="mb-2 flex items-baseline gap-2">
        <h4
          id={labelId}
          className="m-0 text-[10.5px] font-bold uppercase"
          style={{ color: "var(--color-text-4)", letterSpacing: "1px", fontFamily: "var(--font-mono)" }}
        >
          {t("detail_section_source_text")}
        </h4>
        <span className="text-[10px]" style={{ color: "var(--color-text-4)" }}>
          {t("detail_source_text_readonly_hint")}
        </span>
      </div>
      <p
        className="m-0 whitespace-pre-wrap border-l-2 pl-3 text-[12px]"
        style={{
          borderColor: "var(--color-hairline)",
          color: content ? "var(--color-text-3)" : "var(--color-text-4)",
          lineHeight: 1.65,
          fontStyle: content ? undefined : "italic",
        }}
      >
        {content || t("detail_source_text_empty")}
      </p>
    </section>
  );
}
