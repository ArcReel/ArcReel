import { useState } from "react";
import { useTranslation } from "react-i18next";
import { INPUT_CLS } from "@/components/ui/darkroom-tokens";

/**
 * JSON 片段编辑器。文本是编辑期的真相源，只有解析成功才写回定义——
 * 否则用户敲到一半的中间态会被反复重排，无法输入。
 */
export function JsonBodyEditor({
  value,
  onChange,
  readOnly,
  rows,
  ariaLabel,
}: {
  value: unknown;
  onChange: (next: unknown) => void;
  readOnly?: boolean;
  rows?: number;
  ariaLabel: string;
}) {
  const { t } = useTranslation("dashboard");
  const [text, setText] = useState(() => JSON.stringify(value ?? {}, null, 2));
  const [invalid, setInvalid] = useState(false);

  return (
    <div>
      <textarea
        value={text}
        readOnly={readOnly}
        aria-label={ariaLabel}
        aria-invalid={invalid || undefined}
        spellCheck={false}
        rows={rows ?? 8}
        onChange={(e) => {
          setText(e.target.value);
          try {
            const parsed: unknown = JSON.parse(e.target.value);
            setInvalid(false);
            onChange(parsed);
          } catch {
            setInvalid(true);
          }
        }}
        className={`${INPUT_CLS} resize-y font-mono text-[11.5px] leading-[1.6]`}
      />
      {invalid && (
        <span role="alert" className="mt-1.5 block text-[12px] text-warm-bright">
          {t("ce_json_parse_error")}
        </span>
      )}
    </div>
  );
}
