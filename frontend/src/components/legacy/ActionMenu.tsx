import { useId, useRef, useState, type ComponentType, type CSSProperties, type ReactNode } from "react";

import { GlassPopover } from "./GlassPopover";

export interface ActionMenuItem {
  key: string;
  label: string;
  icon?: ComponentType<{ className?: string }>;
  onSelect: () => void;
  disabled?: boolean;
  /** 不可撤销的操作（如删除），以暖色提示。 */
  danger?: boolean;
  /** 禁用时的原因。 */
  title?: string;
}

interface ActionMenuProps {
  /** 触发按钮的无障碍名称与悬停提示。 */
  label: string;
  children: ReactNode;
  items: ActionMenuItem[];
  triggerClassName?: string;
  triggerStyle?: CSSProperties;
  width?: string;
}

/** 按钮触发的操作菜单：点选一项即执行并收起。 */
export function ActionMenu({ label, children, items, triggerClassName, triggerStyle, width = "w-52" }: ActionMenuProps) {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();
  return (
    <>
      <button
        ref={anchorRef}
        type="button"
        aria-label={label}
        title={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={(event) => {
          event.stopPropagation();
          setOpen((value) => !value);
        }}
        className={triggerClassName}
        style={triggerStyle}
      >
        {children}
      </button>
      <GlassPopover
        open={open}
        onClose={() => setOpen(false)}
        anchorRef={anchorRef}
        sideOffset={4}
        width={width}
        align="start"
        showHairline={false}
      >
        <div id={menuId} role="menu" aria-label={label} className="py-1">
          {items.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.key}
                type="button"
                role="menuitem"
                disabled={item.disabled}
                title={item.title}
                onClick={() => {
                  setOpen(false);
                  item.onSelect();
                }}
                className="focus-ring flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12.5px] transition-colors hover:bg-[oklch(0.26_0.012_265/0.5)] disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-transparent"
                style={{ color: item.danger ? "var(--warn)" : "var(--subtle-foreground)" }}
              >
                {Icon ? <Icon className="h-3.5 w-3.5 shrink-0" /> : null}
                <span className="truncate">{item.label}</span>
              </button>
            );
          })}
        </div>
      </GlassPopover>
    </>
  );
}
