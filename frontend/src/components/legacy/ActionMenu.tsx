import type { ComponentType, CSSProperties, ReactNode } from "react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export interface ActionMenuItem {
  key: string;
  label: string;
  icon?: ComponentType<{ className?: string }>;
  onSelect: () => void;
  disabled?: boolean;
  /** 不可撤销的操作（如删除），以危险色提示。 */
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

/**
 * 按钮触发的操作菜单：点选一项即执行并收起，方向键在菜单项间移动。
 * DropdownMenu 的薄封装，新代码直接用 `components/ui/dropdown-menu`，调用处由各区域逐步替换。
 */
export function ActionMenu({ label, children, items, triggerClassName, triggerStyle, width = "w-52" }: ActionMenuProps) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={label}
        title={label}
        // 触发按钮常放在可点击的卡片里，点击不冒泡到卡片
        onClick={(event) => event.stopPropagation()}
        className={triggerClassName}
        style={triggerStyle}
      >
        {children}
      </DropdownMenuTrigger>
      <DropdownMenuContent aria-label={label} className={width}>
        {items.map((item) => {
          const Icon = item.icon;
          return (
            <DropdownMenuItem
              key={item.key}
              disabled={item.disabled}
              title={item.title}
              variant={item.danger ? "destructive" : "default"}
              onClick={item.onSelect}
            >
              {Icon ? <Icon /> : null}
              <span className="truncate">{item.label}</span>
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
