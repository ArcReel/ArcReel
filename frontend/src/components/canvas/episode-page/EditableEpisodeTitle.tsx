import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Pencil, X } from "lucide-react";

import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface EditableEpisodeTitleProps {
  title: string;
  /** 标题为空时展示的集名（按播出位置派生的「第 N 集」），也是输入框的占位文字。 */
  placeholder?: string;
  /** 保存回调；reject 时组件保持编辑态，错误提示由调用方负责（如 toast）。 */
  onSave: (next: string) => Promise<void>;
  /** false 时纯展示、不暴露编辑入口（如无剧本文件的分集）。 */
  canEdit: boolean;
}

/**
 * 集页页头的集标题：点铅笔进入行内输入，Enter 保存，Esc 取消还原；清空后保存即回到派生集名（placeholder）。
 * canEdit=false 时只显示标题。
 */
export function EditableEpisodeTitle({ title, placeholder, onSave, canEdit }: EditableEpisodeTitleProps) {
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

  const enterEdit = () => {
    setDraft(title);
    setIsEditing(true);
  };

  const cancel = () => {
    setDraft(title);
    setIsEditing(false);
  };

  const save = async () => {
    if (saving) return;
    if (trimmed === title.trim()) {
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

  const heading = (
    <h1 className="min-w-0 text-base font-semibold text-foreground">
      <TruncatedText text={title || placeholder || ""} />
    </h1>
  );

  if (!canEdit) return heading;

  if (isEditing) {
    return (
      <div className="flex min-w-0 items-center gap-1">
        <Input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={placeholder}
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
          className="h-7 w-64 min-w-0"
        />
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => void save()}
          disabled={saving}
          aria-label={t("common:save")}
        >
          <Check aria-hidden />
        </Button>
        <Button variant="ghost" size="icon-sm" onClick={cancel} disabled={saving} aria-label={t("common:cancel")}>
          <X aria-hidden />
        </Button>
      </div>
    );
  }

  return (
    <div className="flex min-w-0 items-center gap-0.5">
      {heading}
      <Button variant="ghost" size="icon-xs" onClick={enterEdit} aria-label={t("edit_episode_title")}>
        <Pencil aria-hidden />
      </Button>
    </div>
  );
}
