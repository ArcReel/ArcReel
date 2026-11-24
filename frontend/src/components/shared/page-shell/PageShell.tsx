import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cn } from "cn";

export type ContainerTier = "constrained" | "full" | "bleed";

interface FooterSlot {
  tier: Exclude<ContainerTier, "bleed">;
  element: HTMLElement | null;
}

const FooterSlotContext = createContext<FooterSlot | null>(null);

interface PageShellProps {
  header: ReactNode;
  sidebar?: ReactNode;
  tier: ContainerTier;
  children: ReactNode;
}

export function PageShell({ header, sidebar, tier, children }: PageShellProps) {
  const [footerElement, setFooterElement] = useState<HTMLDivElement | null>(null);
  const footerSlot = useMemo<FooterSlot | null>(
    () => (tier === "bleed" ? null : { tier, element: footerElement }),
    [tier, footerElement],
  );

  return (
    <div className="relative flex h-dvh flex-col overflow-hidden text-foreground">
      {header}
      <div className="flex min-h-0 flex-1">
        {sidebar}
        <div className="flex min-w-0 flex-1 flex-col">
          <FooterSlotContext.Provider value={footerSlot}>
            {tier === "bleed" ? (
              <main className="@container/page relative flex min-h-0 min-w-0 flex-1 flex-col">{children}</main>
            ) : (
              <main className="relative min-h-0 min-w-0 flex-1 overflow-y-auto [scrollbar-gutter:stable]">
                <div className="p-6 xl:p-8">
                  <div className={cn("@container/page min-w-0", tier === "constrained" && "max-w-190")}>
                    {children}
                  </div>
                </div>
              </main>
            )}
          </FooterSlotContext.Provider>
          {tier !== "bleed" && (
            <div
              ref={setFooterElement}
              className="shrink-0 border-t border-border bg-card px-6 py-3 empty:hidden xl:px-8"
            />
          )}
        </div>
      </div>
    </div>
  );
}

export function PageShellFooter({ children, constrained = false }: { children: ReactNode; constrained?: boolean }) {
  const slot = useContext(FooterSlotContext);
  if (!slot) return children;
  if (!slot.element) return null;
  return createPortal(
    <div className={cn("min-w-0", (constrained || slot.tier === "constrained") && "max-w-190")}>{children}</div>,
    slot.element,
  );
}
