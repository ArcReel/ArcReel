import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Clapperboard,
  Film,
  FolderCog,
  LayoutGrid,
  Scissors,
  ScrollText,
  Search,
  Users,
  Zap,
} from "lucide-react";
import { useAssistantStore } from "@/stores/assistant-store";

/** Per-skill metadata: Chinese label + icon */
const SKILL_META: Record<string, { label: string; icon: LucideIcon }> = {
  "manga-workflow":      { label: "视频工作流",   icon: Clapperboard },
  "generate-script":     { label: "生成剧本",     icon: ScrollText },
  "generate-storyboard": { label: "生成分镜图",   icon: LayoutGrid },
  "generate-video":      { label: "生成视频",     icon: Film },
  "generate-characters": { label: "生成人物图",   icon: Users },
  "generate-clues":      { label: "生成线索图",   icon: Search },
  "compose-video":       { label: "合成视频",     icon: Scissors },
  "manage-project":      { label: "项目管理",     icon: FolderCog },
};

export interface SlashCommandMenuHandle {
  /** Returns true if the key was consumed (caller should preventDefault). */
  handleKeyDown: (key: string) => boolean;
  /** ID of the currently active option for aria-activedescendant. */
  activeDescendantId: string | undefined;
}

interface SlashCommandMenuProps {
  readonly filter: string;
  readonly onSelect: (command: string) => void;
}

const MENU_ID = "slash-command-menu";

/**
 * Slash command popover — appears above the input when user types "/".
 * Filters skills by the text after "/", supports keyboard navigation.
 */
export const SlashCommandMenu = forwardRef<SlashCommandMenuHandle, SlashCommandMenuProps>(
  function SlashCommandMenu({ filter, onSelect }, ref) {
    const { skills } = useAssistantStore();
    const [activeIndex, setActiveIndex] = useState(0);

    const query = filter.toLowerCase();
    const filtered = skills.filter(
      (s) =>
        s.name.toLowerCase().includes(query) ||
        s.description.toLowerCase().includes(query) ||
        (SKILL_META[s.name]?.label ?? "").includes(query),
    );

    // Reset active index when filter or list changes
    useEffect(() => {
      setActiveIndex(0);
    }, [filter, filtered.length]);

    // Scroll active item into view
    const itemRefs = useRef<Map<number, HTMLButtonElement>>(new Map());
    useEffect(() => {
      itemRefs.current.get(activeIndex)?.scrollIntoView?.({ block: "nearest" });
    }, [activeIndex]);

    // Expose keyboard handler to parent
    useImperativeHandle(ref, () => ({
      handleKeyDown(key: string): boolean {
        if (filtered.length === 0) return false;
        switch (key) {
          case "ArrowDown":
            setActiveIndex((prev) => (prev + 1) % filtered.length);
            return true;
          case "ArrowUp":
            setActiveIndex((prev) => (prev - 1 + filtered.length) % filtered.length);
            return true;
          case "Enter": {
            const skill = filtered[activeIndex];
            if (skill) onSelect(`/${skill.name}`);
            return true;
          }
          case "Escape":
            return true; // parent handles close
          default:
            return false;
        }
      },
      get activeDescendantId() {
        return filtered.length > 0 ? `${MENU_ID}-option-${activeIndex}` : undefined;
      },
    }), [activeIndex, filtered, onSelect]);

    if (filtered.length === 0) return null;

    return (
      <div
        id={MENU_ID}
        role="listbox"
        aria-label="技能命令菜单"
        className="absolute bottom-full left-0 right-0 mb-1 max-h-52 overflow-y-auto rounded-lg border border-gray-700 bg-gray-900 py-1 shadow-xl"
      >
        {filtered.map((skill, i) => {
          const meta = SKILL_META[skill.name];
          const Icon = meta?.icon ?? Zap;
          const label = meta?.label;
          const isActive = i === activeIndex;
          return (
            <button
              key={skill.name}
              ref={(el) => {
                if (el) itemRefs.current.set(i, el);
                else itemRefs.current.delete(i);
              }}
              id={`${MENU_ID}-option-${i}`}
              role="option"
              aria-selected={isActive}
              type="button"
              // Use onMouseDown + preventDefault to keep textarea focus
              onMouseDown={(e) => {
                e.preventDefault();
                onSelect(`/${skill.name}`);
              }}
              onMouseEnter={() => setActiveIndex(i)}
              className={`flex w-full items-start gap-2 px-3 py-2 text-left text-sm transition-colors ${
                isActive ? "bg-gray-800" : "hover:bg-gray-800"
              }`}
            >
              <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-indigo-400" />
              <div className="min-w-0">
                <span className="font-medium text-gray-200">
                  {label && <>{label}<span className="ml-1.5 text-gray-500">/{skill.name}</span></>}
                  {!label && <>/{skill.name}</>}
                </span>
                <p className="truncate text-xs text-gray-500">{skill.description}</p>
              </div>
            </button>
          );
        })}
      </div>
    );
  },
);
