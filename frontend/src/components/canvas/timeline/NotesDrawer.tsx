import { useEffect, useRef, useState } from "react";
import { StickyNote, X } from "lucide-react";
import { useTranslation } from "react-i18next";

interface NotesDrawerProps {
  /** 当前 shot 的 ID（用于 placeholder） */
  shotId: string;
  /** 持久化值 */
  value: string;
  /** 失焦或显式提交时调用，参数即 textarea 当前值 */
  onCommit: (value: string) => void;
}

/**
 * Shot 备注抽屉。触发按钮 + 弹层 textarea，blur 时持久化。
 */
export function NotesDrawer({ shotId, value, onCommit }: NotesDrawerProps) {
  const { t } = useTranslation("dashboard");
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  const committedRef = useRef(value);

  useEffect(() => {
    if (committedRef.current !== value) {
      setDraft(value);
      committedRef.current = value;
    }
  }, [value]);

  const handleClose = () => {
    if (draft !== committedRef.current) {
      committedRef.current = draft;
      onCommit(draft);
    }
    setOpen(false);
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        title={t("shot_notes_button")}
        className="sv-navbtn relative inline-flex items-center gap-1.5 px-2"
        style={{
          color: open
            ? "var(--color-accent-2)"
            : value
              ? "var(--color-text-2)"
              : "var(--color-text-3)",
          background: open
            ? "var(--color-accent-dim)"
            : value
              ? "oklch(0.24 0.012 265 / 0.7)"
              : "oklch(0.22 0.011 265 / 0.5)",
          borderColor: open ? "var(--color-accent-soft)" : "var(--color-hairline)",
        }}
      >
        <StickyNote className="h-3.5 w-3.5" />
        {value && !open && (
          <span
            aria-hidden="true"
            className="absolute right-0.5 top-0.5 h-[5px] w-[5px] rounded-full"
            style={{
              background: "var(--color-accent)",
              boxShadow: "0 0 4px var(--color-accent-glow)",
            }}
          />
        )}
      </button>

      {open && (
        <>
          <div
            onClick={handleClose}
            className="fixed inset-0 z-30"
            aria-hidden="true"
          />
          <div
            className="absolute z-40 max-w-[calc(100vw-32px)] rounded-[10px] p-3"
            style={{
              top: "calc(100% + 6px)",
              right: 14,
              width: 340,
              background:
                "linear-gradient(180deg, oklch(0.21 0.012 265 / 0.98), oklch(0.18 0.010 265 / 0.98))",
              border: "1px solid var(--color-hairline)",
              boxShadow:
                "0 24px 60px -20px oklch(0 0 0 / 0.7), 0 0 0 1px var(--color-hairline-soft)",
              backdropFilter: "blur(12px)",
              WebkitBackdropFilter: "blur(12px)",
            }}
          >
            <div className="mb-2 flex items-center gap-1.5">
              <StickyNote
                className="h-3.5 w-3.5"
                style={{ color: "var(--color-text-3)" }}
              />
              <span
                className="text-[11px] font-bold uppercase"
                style={{
                  color: "var(--color-text-3)",
                  letterSpacing: "0.8px",
                  fontFamily: "var(--font-mono)",
                }}
              >
                {t("shot_notes_title")}
              </span>
              <span className="flex-1" />
              <span
                className="num text-[10px]"
                style={{ color: "var(--color-text-4)" }}
              >
                {draft.length}
              </span>
              <button
                type="button"
                onClick={handleClose}
                aria-label={t("shot_notes_close")}
                className="grid h-5 w-5 place-items-center rounded text-sm leading-none focus-ring"
                style={{ color: "var(--color-text-4)" }}
              >
                <X className="h-3 w-3" />
              </button>
            </div>
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              ref={(el) => {
                // 抽屉打开时聚焦，避免使用 autoFocus prop 触发 a11y lint
                if (el) requestAnimationFrame(() => el.focus());
              }}
              placeholder={t("shot_notes_placeholder", { id: shotId })}
              className="w-full resize-y rounded-md p-2.5 text-[12.5px] outline-none focus-ring"
              style={{
                minHeight: 140,
                lineHeight: 1.55,
                color: "var(--color-text-2)",
                background: "oklch(0.16 0.010 265 / 0.6)",
                border: "1px solid var(--color-hairline-soft)",
                fontFamily: "var(--font-sans)",
              }}
            />
          </div>
        </>
      )}
    </>
  );
}
