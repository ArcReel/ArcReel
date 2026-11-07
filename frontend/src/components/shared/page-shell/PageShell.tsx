import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cn } from "cn";

/**
 * Content-container modes.
 * - `constrained`: left-aligned columns capped at 760px; shell bodies scroll/provide padding. For forms/settings.
 * - `full`: uncapped, with shell body scrolling/padding. For lists/grids/tables.
 * - `bleed`: shell bodies lack scrolling/padding; sections fill height and own columns, each scrolling independently. For master/detail layouts/editors.
 */
export type ContainerTier = "constrained" | "full" | "bleed";

interface FooterSlot {
  tier: Exclude<ContainerTier, "bleed">;
  element: HTMLElement | null;
}

const FooterSlotContext = createContext<FooterSlot | null>(null);

interface PageShellProps {
  /** Headers, supplied as `PageHeader`. */
  header: ReactNode;
  /** Sidebars, supplied as `PageSidebar`; omitted by single-view project lobbies/asset libraries. */
  sidebar?: ReactNode;
  tier: ContainerTier;
  children: ReactNode;
}

/**
 * Shared global-settings/project-settings/project-lobby/asset-library shells: full-width headers, left sidebars, content columns aligned immediately right of sidebars;
 * extra wide-window whitespace stays right. Shell roots fill viewport heights; documents never scroll. Scrolling belongs only to sidebars, shell bodies
 * (constrained/full modes) or individual bleed-section columns.
 *
 * Only shells switch standard/compact modes by viewport (`xl`, 1280): sidebar widths 224 / 200px and content padding 32 / 24px.
 * Content columns are named `page` size containers; sections respond to width through queries such as `@md/page:`.
 *
 * Constrained/full modes have fixed save-bar rows beneath bodies, populated by section `PageShellFooter` rendering;
 * absent save bars hide rows. Bleed mode has no row; save bars live at section-owned detail-column bottoms.
 */
export function PageShell({ header, sidebar, tier, children }: PageShellProps) {
  const [footerElement, setFooterElement] = useState<HTMLDivElement | null>(null);
  const footerSlot = useMemo<FooterSlot | null>(
    () => (tier === "bleed" ? null : { tier, element: footerElement }),
    [tier, footerElement],
  );

  return (
    // Shell roots/all scroll containers are positioned: absolutely positioned children such as sr-only use them as containing blocks,
    // never escaping scroll containers to expand documents.
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
            // Sections render through PageShellFooter; empty rows hide entirely.
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

/**
 * Render save bars in fixed shell bottom rows, outside body scrolling, with inner widths/origins matching content columns.
 * Full-mode forms independently capped at 760px pass `constrained` to match save-bar widths.
 * Render in place outside shells (e.g. direct section unit tests) or in bleed mode.
 */
export function PageShellFooter({ children, constrained = false }: { children: ReactNode; constrained?: boolean }) {
  const slot = useContext(FooterSlotContext);
  if (!slot) return children;
  if (!slot.element) return null;
  return createPortal(
    <div className={cn("min-w-0", (constrained || slot.tier === "constrained") && "max-w-190")}>{children}</div>,
    slot.element,
  );
}
