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
const FREE_IMAGE_COMMAND: SkillInfo = {
  name: "generate-img",
  description: "直接调用图片模型生成一张图片，结果保存到草稿库",
  scope: "project",
  path: "local",
  icon: "images",
};

const ICON_MAP: Record<string, LucideIcon> = {
  clapperboard: Clapperboard,
  images: Images,
  "grid-2x2": Grid2x2,
  film: Film,
  users: Users,
  scissors: Scissors,
  "audio-lines": AudioLines,
};

function skillLabel(t: TFunction, skillName: string): string | undefined {
  const value = t(`dashboard:skill_name_${skillName.replace(/-/g, "_")}`, { defaultValue: undefined });
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

export function useSlashCommands(filter: string | null): SkillInfo[] {
  const { t } = useTranslation("dashboard");
  const skills = useAssistantStore((s) => s.skills);
  return useMemo(() => {
    if (filter === null) return [];
    const query = filter.toLowerCase();
    const available = query.includes("generate") ? [FREE_IMAGE_COMMAND, ...skills] : skills;
    // 后端已滤掉用户不能直接调用的技能
    return available.filter(
      (skill) =>
        skill.name.toLowerCase().includes(query) ||
        skill.description.toLowerCase().includes(query) ||
        (skillLabel(t, skill.name) ?? "").toLowerCase().includes(query),
    );
  }, [filter, skills, t]);
}

interface SlashCommandMenuProps {
  anchor: RefObject<HTMLElement | null>;
  skills: SkillInfo[];
  active: string | undefined;
  onActiveChange: (name: string) => void;
  onSelect: (command: string) => void;
  onClose: () => void;
  onIdsChange: (listId: string | undefined, activeId: string | undefined) => void;
}


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
      // react-doctor-disable-next-line react-doctor/no-prop-callback-in-effect -- 回传的是 cmdk 自己生成的 DOM id（外部系统），本组件没有可提升的 state，只能在 DOM 落定后读出并通知输入框
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
          <CommandList ref={setList} label={t("slash_menu_label")}>
            <CommandGroup>
              {skills.map((skill) => {
                const Icon = (skill.icon && ICON_MAP[skill.icon]) || Zap;
                const label = skill.name === FREE_IMAGE_COMMAND.name ? t("slash_generate_img_title") : skillLabel(t, skill.name);
                return (
                  <CommandItem
                    key={skill.name}
                    value={skill.name}
                    onSelect={() => onSelect(`/${skill.name}`)}
                    onMouseDown={(event) => event.preventDefault()}
                    className="items-start"
                  >
                    <Icon aria-hidden className="mt-0.5 text-primary" />
                    <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                      <span className="truncate">
                        {label ?? `/${skill.name}`}
                        <span className="ml-1.5 text-muted-foreground">/{skill.name}</span>
                      </span>
                      <span className="truncate text-xs text-muted-foreground">{skill.name === FREE_IMAGE_COMMAND.name ? t("slash_generate_img_description") : skill.description}</span>
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
