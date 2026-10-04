import { useLayoutEffect, useMemo, useState, type RefObject } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import type { LucideIcon } from "lucide-react";
import { AudioLines, Clapperboard, Film, Grid2x2, Images, Scissors, Users, Zap } from "lucide-react";
import { Command, CommandGroup, CommandItem, CommandList } from "@/components/ui/command";
import { Popover, PopoverContent } from "@/components/ui/popover";
import { useAssistantStore } from "@/stores/assistant-store";
import type { SkillInfo } from "@/types";

/** 接口给出的 Lucide 图标名 → 图标组件。 */
const ICON_MAP: Record<string, LucideIcon> = {
  clapperboard: Clapperboard,
  images: Images,
  "grid-2x2": Grid2x2,
  film: Film,
  users: Users,
  scissors: Scissors,
  "audio-lines": AudioLines,
};

/** 技能的本地化显示名；没有译名时返回 undefined，由调用方回退到 /技能名。 */
function skillLabel(t: TFunction, skillName: string): string | undefined {
  const value = t(`dashboard:skill_name_${skillName.replace(/-/g, "_")}`, { defaultValue: undefined });
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

/** 按「/」后面的文字筛选技能：匹配技能名、说明或本地化显示名。 */
export function useSlashCommands(filter: string | null): SkillInfo[] {
  const { t } = useTranslation("dashboard");
  const skills = useAssistantStore((s) => s.skills);
  return useMemo(() => {
    if (filter === null) return [];
    const query = filter.toLowerCase();
    // 后端已滤掉用户不能直接调用的技能
    return skills.filter(
      (skill) =>
        skill.name.toLowerCase().includes(query) ||
        skill.description.toLowerCase().includes(query) ||
        (skillLabel(t, skill.name) ?? "").toLowerCase().includes(query),
    );
  }, [filter, skills, t]);
}

interface SlashCommandMenuProps {
  /** 输入框外框：菜单浮在它的上方，与它同宽。 */
  anchor: RefObject<HTMLElement | null>;
  /** 已筛选的技能；为空时不显示菜单。 */
  skills: SkillInfo[];
  /** 当前高亮的技能名，由输入框的方向键驱动。 */
  active: string | undefined;
  onActiveChange: (name: string) => void;
  onSelect: (command: string) => void;
  onClose: () => void;
  /** 列表与高亮项渲染后的 DOM id，供输入框的 aria-controls 与 aria-activedescendant 引用。 */
  onIdsChange: (listId: string | undefined, activeId: string | undefined) => void;
}

// ---------------------------------------------------------------------------
// SlashCommandMenu — 输入「/」时浮在输入框上方的技能菜单。
// 焦点始终留在输入框里：方向键与回车由输入框处理，菜单只负责展示与指针选择。
// ---------------------------------------------------------------------------

export function SlashCommandMenu({
  anchor,
  skills,
  active,
  onActiveChange,
  onSelect,
  onClose,
  onIdsChange,
}: SlashCommandMenuProps) {
  const { t } = useTranslation("dashboard");
  const open = skills.length > 0;
  const activeName = useMemo(
    () => (active && skills.some((skill) => skill.name === active) ? active : skills[0]?.name),
    [active, skills],
  );

  // cmdk 给列表与选项生成自己的 id，并在自己的提交里异步更新选中项；输入框要引用它们，只能等 DOM
  // 落定后读出来。弹层在打开后的下一次提交才挂上列表，所以列表元素记在 state 里，挂上后监听变化
  const [list, setList] = useState<HTMLDivElement | null>(null);
  useLayoutEffect(() => {
    if (!open || !list) {
      onIdsChange(undefined, undefined);
      return;
    }
    const sync = () => {
      const activeItem = list.querySelector<HTMLElement>('[cmdk-item][aria-selected="true"]');
      onIdsChange(list.id || undefined, activeItem?.id || undefined);
      activeItem?.scrollIntoView?.({ block: "nearest" });
    };
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(list, { subtree: true, childList: true, attributes: true, attributeFilter: ["aria-selected"] });
    return () => observer.disconnect();
  }, [open, list, onIdsChange]);

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <PopoverContent
        anchor={anchor}
        side="top"
        align="start"
        sideOffset={6}
        initialFocus={false}
        finalFocus={false}
        className="w-(--anchor-width)"
      >
        <Command
          shouldFilter={false}
          loop
          value={activeName ?? ""}
          onValueChange={onActiveChange}
          label={t("slash_menu_label")}
          className="-m-1.5"
        >
          {/* cmdk 列表的可访问名称取自 label，默认是英文「Suggestions」 */}
          <CommandList ref={setList} label={t("slash_menu_label")}>
            <CommandGroup>
              {skills.map((skill) => {
                const Icon = (skill.icon && ICON_MAP[skill.icon]) || Zap;
                const label = skillLabel(t, skill.name);
                return (
                  <CommandItem
                    key={skill.name}
                    value={skill.name}
                    onSelect={() => onSelect(`/${skill.name}`)}
                    // 按下时不让输入框失焦，选择后光标仍在原处
                    onMouseDown={(event) => event.preventDefault()}
                    className="items-start"
                  >
                    <Icon aria-hidden className="mt-0.5 text-primary" />
                    <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                      <span className="truncate">
                        {label ?? `/${skill.name}`}
                        {label && <span className="ml-1.5 text-muted-foreground">/{skill.name}</span>}
                      </span>
                      <span className="truncate text-xs text-muted-foreground">{skill.description}</span>
                    </span>
                  </CommandItem>
                );
              })}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
