import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Pencil, X } from "lucide-react";

import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface EditableEpisodeTitleProps {
  title: string;
  /** Episode display name derived from airing position when title is empty (Episode N), also the input placeholder. */
  placeholder?: string;
  /** Save callback; rejection preserves editing, with caller-owned error feedback such as toasts. */
  onSave: (next: string) => Promise<void>;
  /** False means display only without edit entry, e.g. episodes lacking script files. */
  canEdit: boolean;
}

/**
 * Episode-header title: pencil opens inline input, Enter saves, Esc cancels/restores; saving empty returns to derived name/placeholder.
 * canEdit=false displays the title only.
 */
export function EditableEpisodeTitle({ title, placeholder, onSave, canEdit }: EditableEpisodeTitleProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const [saving, setSaving] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // draft is read only while editing; enterEdit/cancel always reseeds from current title before editing,
  // so no effect must synchronize title props; display mode directly renders the prop.
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
      // Failure keeps editing; callers show error toasts.
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
            // IME composition (e.g. Chinese pinyin) uses Enter to confirm candidates, not to save.
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
