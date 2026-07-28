import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Pencil, X } from "lucide-react";

interface EditableEpisodeTitleProps {
  title: string;
  /** 保存回调；reject 时组件保持编辑态，错误提示由调用方负责（如 toast）。 */
  onSave: (next: string) => Promise<void>;
  /** false 时纯展示、不暴露编辑入口（如无剧本文件的分集）。 */
  canEdit: boolean;
  /** 与各 header 现有标题排版保持一致（timeline 与 reference 字体不同）。 */
  headingClassName?: string;
  headingStyle?: React.CSSProperties;
}

/**
 * 分集标题的可编辑包装：展示态 hover 出铅笔，点击进入 inline 输入，Enter 保存 / Esc 取消还原。
 * 空/纯空白禁用保存。canEdit=false 时退化为纯 <h1> 展示。timeline / reference / grid 三处 header 复用。
 */
export function EditableEpisodeTitle({
  title,
  onSave,
  canEdit,
  headingClassName,
  headingStyle,
}: EditableEpisodeTitleProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const [saving, setSaving] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // draft 仅在编辑态被读取，进入编辑前必经 enterEdit/cancel 用当前 title 重新播种，
  // 故无需 effect 跟随 title prop 同步（展示态直接渲染 title prop）。
  useEffect(() => {
    if (isEditing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [isEditing]);

  const trimmed = draft.trim();
  const canSave = trimmed.length > 0 && !saving;

  const enterEdit = () => {
    setDraft(title);
    setIsEditing(true);
  };

  const cancel = () => {
    setDraft(title);
    setIsEditing(false);
  };

  const save = async () => {
    if (!canSave) return;
    if (trimmed === title) {
      setIsEditing(false);
      return;
    }
    setSaving(true);
    try {
      await onSave(trimmed);
      setIsEditing(false);
    } catch {
      // 失败保持编辑态，错误提示由调用方 toast
    } finally {
      setSaving(false);
    }
  };

  if (!canEdit) {
    return (
      <h1 className={headingClassName} style={headingStyle}>
        {title}
      </h1>
    );
  }

  if (isEditing) {
    return (
      <div className="flex items-center gap-2">
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            // 输入法组合输入中（如中文拼音）按 Enter 是在确认候选词，不应触发保存
            if (e.nativeEvent.isComposing) return;
            if (e.key === "Enter") {
              e.preventDefault();
              void save();
            } else if (e.key === "Escape") {
              e.preventDefault();
              cancel();
            }
          }}
          disabled={saving}
          aria-label={t("edit_episode_title")}
          className={`focus-ring min-w-0 flex-1 rounded border-b bg-transparent outline-none ${headingClassName ?? ""}`}
          style={{ ...headingStyle, borderColor: "var(--color-accent-soft)" }}
        />
        <button
          type="button"
          onClick={() => void save()}
          disabled={!canSave}
          title={t("common:save")}
          aria-label={t("common:save")}
          className="focus-ring inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md transition-colors hover:bg-[oklch(1_0_0_/_0.06)] disabled:opacity-40"
          style={{ color: "var(--color-accent-2)" }}
        >
          <Check className="h-4 w-4" />
        </button>
        <button
          type="button"
          onClick={cancel}
          disabled={saving}
          title={t("common:cancel")}
          aria-label={t("common:cancel")}
          className="focus-ring inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md transition-colors hover:bg-[oklch(1_0_0_/_0.06)] disabled:opacity-40"
          style={{ color: "var(--color-text-3)" }}
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    );
  }

  return (
    <div className="group flex items-center gap-2">
      <h1 className={`min-w-0 ${headingClassName ?? ""}`} style={headingStyle}>
        {title}
      </h1>
      <button
        type="button"
        onClick={enterEdit}
        title={t("edit_episode_title")}
        aria-label={t("edit_episode_title")}
        className="focus-ring inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[var(--color-text-3)] opacity-0 transition-[opacity,background-color] hover:bg-[oklch(1_0_0_/_0.06)] hover:text-[var(--color-text)] focus-visible:opacity-100 group-hover:opacity-100"
      >
        <Pencil className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
