import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Textarea } from "@/components/ui/textarea";

/**
 * JSON fragment editor. The text is the source of truth during editing; write back to the definition only after parsing succeeds.
 * Otherwise, incomplete input would be repeatedly reformatted, preventing typing.
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
      <Textarea
        mono
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
      />
      {invalid && (
        <span role="alert" className="mt-1.5 block text-xs text-warn">
          {t("ce_json_parse_error")}
        </span>
      )}
    </div>
  );
}
