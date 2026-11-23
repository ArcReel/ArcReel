import type { ReactNode } from "react";

export function TabHeader({ title, description }: { title: string; description?: ReactNode }) {
  return (
    <header className="flex flex-col gap-1">
      <h2 className="text-lg font-medium">{title}</h2>
      {description && <p className="max-w-[40em] text-sm text-muted-foreground">{description}</p>}
    </header>
  );
}

export function SettingsBlock({
  title,
  titleId,
  description,
  aside,
  children,
}: {
  title: string;
  titleId?: string;
  description?: ReactNode;
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
