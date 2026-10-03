import type { ReactNode } from "react";

/** 分页页头：只显示分页名，可选一行说明。 */
export function TabHeader({ title, description }: { title: string; description?: ReactNode }) {
  return (
    <header className="flex flex-col gap-1">
      <h2 className="text-lg font-medium">{title}</h2>
      {description && <p className="max-w-[40em] text-sm text-muted-foreground">{description}</p>}
    </header>
  );
}

/** 分页里的一个分区：标题、可选说明与内容，相邻分区之间一条细线。 */
export function SettingsBlock({
  title,
  titleId,
  description,
  aside,
  children,
}: {
  title: string;
  /** 内容需要以标题作可访问名称时（如单选组的 `aria-labelledby`）传入。 */
  titleId?: string;
  description?: ReactNode;
  /** 标题行右侧的标记，如「创建后不可更改」。 */
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3 border-t border-border pt-6 first:border-t-0 first:pt-0">
      <div className="flex flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <h3 id={titleId} className="text-sm font-medium text-foreground">
            {title}
          </h3>
          {aside}
        </div>
        {description && <p className="max-w-[40em] text-sm text-muted-foreground">{description}</p>}
      </div>
      {children}
    </section>
  );
}
