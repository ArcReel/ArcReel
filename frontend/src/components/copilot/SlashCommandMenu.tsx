import { useLayoutEffect, useMemo, useState, type RefObject } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import type { LucideIcon } from "lucide-react";
import { AudioLines, Clapperboard, Film, Grid2x2, Images, Scissors, Users, Zap } from "lucide-react";
import { Command, CommandGroup, CommandItem, CommandList } from "@/components/ui/command";
import { Popover, PopoverContent } from "@/components/ui/popover";
import { useAssistantStore } from "@/stores/assistant-store";
import type { SkillInfo } from "@/types";

/** API Lucide icon names → components. */
const ICON_MAP: Record<string, LucideIcon> = {
  clapperboard: Clapperboard,
  images: Images,
  "grid-2x2": Grid2x2,
  film: Film,
  users: Users,
  scissors: Scissors,
  "audio-lines": AudioLines,
};

/** Localized skill names; undefined without translations lets callers fall back to /skill names. */
function skillLabel(t: TFunction, skillName: string): string | undefined {
  const value = t(`dashboard:skill_name_${skillName.replace(/-/g, "_")}`, { defaultValue: undefined });
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

/** Filter skills by text after /, matching names/descriptions/localized display names. */
export function useSlashCommands(filter: string | null): SkillInfo[] {
  const { t } = useTranslation("dashboard");
  const skills = useAssistantStore((s) => s.skills);
  return useMemo(() => {
    if (filter === null) return [];
    const query = filter.toLowerCase();
    // Backends already exclude skills unavailable for direct user invocation.
    return skills.filter(
      (skill) =>
        skill.name.toLowerCase().includes(query) ||
        skill.description.toLowerCase().includes(query) ||
        (skillLabel(t, skill.name) ?? "").toLowerCase().includes(query),
    );
  }, [filter, skills, t]);
}

interface SlashCommandMenuProps {
  /** Input frames: menus float above them at matching widths. */
  anchor: RefObject<HTMLElement | null>;
  /** Filtered skills; empty lists hide menus. */
  skills: SkillInfo[];
  /** Highlighted skill names, driven by input arrow keys. */
  active: string | undefined;
  onActiveChange: (name: string) => void;
  onSelect: (command: string) => void;
  onClose: () => void;
  /** Rendered list/highlight DOM IDs for input aria-controls/aria-activedescendant. */
  onIdsChange: (listId: string | undefined, activeId: string | undefined) => void;
}

// ---------------------------------------------------------------------------
// SlashCommandMenu: skill menus above inputs when typing /.
// Focus stays inside inputs, which handle arrows/Enter; menus only present/pointer-select.
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

  // cmdk generates list/option IDs and updates selections asynchronously during its own commits; inputs can reference IDs only after DOM
  // settlement. Popovers mount lists on the next commit after opening; keep list elements in state and observe changes after mounting.
  const [list, setList] = useState<HTMLDivElement | null>(null);
  useLayoutEffect(() => {
    if (!open || !list) {
      // react-doctor-disable-next-line react-doctor/no-prop-callback-in-effect -- Return cmdk-generated external-system DOM IDs; this component has no liftable state and must read settled DOM then notify inputs
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
          {/* cmdk list accessible names come from label, default English Suggestions. */}
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
                    // Prevent input blur on pointer down, keeping carets in place after selection.
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
