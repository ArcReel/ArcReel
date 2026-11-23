import { useId, useRef } from "react";
import { StickyNote } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTitle, PopoverTrigger } from "@/components/ui/popover";
import { Textarea } from "@/components/ui/textarea";
import { useItemPositionLabel } from "@/components/shared/episode-item/useEpisodeItem";

interface NotesDrawerProps {
  shotId: string;
  value: string;
  onChange: (value: string) => void;
}

export function NotesDrawer({ shotId, value, onChange }: NotesDrawerProps) {
  const { t } = useTranslation("dashboard");
  const itemLabel = useItemPositionLabel(shotId);
  const titleId = useId();
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  return (
    <Popover>
      <PopoverTrigger
        render={<Button variant="ghost" size="icon-sm" className="relative" aria-label={t("shot_notes_button")} />}
      >
        <StickyNote aria-hidden />
        {value ? <span aria-hidden className="absolute top-1 right-1 size-1.5 rounded-full bg-primary" /> : null}
      </PopoverTrigger>
      <PopoverContent align="end" positionMethod="fixed" className="w-85" initialFocus={textareaRef}>
        <div className="flex items-baseline gap-2 px-0.5">
          <PopoverTitle id={titleId} className="min-w-0 flex-1">
            {t("shot_notes_title")}
          </PopoverTitle>
          <span className="num text-xs text-muted-foreground">{value.length}</span>
        </div>
        <Textarea
          ref={textareaRef}
          aria-labelledby={titleId}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={t("shot_notes_placeholder", { id: itemLabel })}
          className="min-h-36"
        />
      </PopoverContent>
    </Popover>
  );
}
