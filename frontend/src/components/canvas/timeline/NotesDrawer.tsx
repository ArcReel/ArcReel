import { useId, useRef } from "react";
import { StickyNote } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTitle, PopoverTrigger } from "@/components/ui/popover";
import { Textarea } from "@/components/ui/textarea";
import { itemIdWithinEpisode } from "@/utils/episode-display";

interface NotesDrawerProps {
  /** 当前 shot 的 ID（用于 placeholder） */
  shotId: string;
  /** 备注的当前内容，包含未保存修改 */
  value: string;
  /** 输入即写入分镜的未保存修改，由分镜详情统一保存 */
  onChange: (value: string) => void;
}

/**
 * 分镜备注：页头行尾的图标按钮打开弹层。备注是分镜的字段，输入写进分镜的未保存修改，
 * 与提示词一起由提示条保存或放弃。有备注时按钮带一个小圆点。
 */
export function NotesDrawer({ shotId, value, onChange }: NotesDrawerProps) {
  const { t } = useTranslation("dashboard");
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
      {/* 弹层落在媒体栏上方，常盖住半个按钮；fixed 定位与对话框同属浮层，axe 的 target-size 不再把被盖住的半个按钮算作过小的目标 */}
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
          placeholder={t("shot_notes_placeholder", { id: itemIdWithinEpisode(shotId) })}
          className="min-h-36"
        />
      </PopoverContent>
    </Popover>
  );
}
