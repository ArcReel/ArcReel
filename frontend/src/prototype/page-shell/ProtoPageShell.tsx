// PROTOTYPE — 页面容器档位与外壳契约（#2969）的三个外壳变体。
// 「三个变体，同一组路由：/app/settings 与 /app/projects/:name/settings，?variant= 切换。」
//
// 三个变体共用同一条滚动契约（来自「滚动失效的系统性排查」）：根 h-dvh、文档不滚动、
// 每个视图只有一个滚动容器（橙色描边），保存栏是外壳的固定底行而不是浮层。
// 它们分歧的是：顶栏放在哪、内容列靠左还是居中、保存栏横跨多宽。

import { useState, type ComponentType, type ReactNode } from "react";
import { ChevronLeft } from "lucide-react";
import { cn } from "@/lib/utils";
import { FooterSlotContext, TIER_MAX, type ShellVariant, type Tier } from "./shell";
import { ShellProtoBar } from "./ShellProtoBar";
import "./page-shell.css";

export interface ShellNavItem {
  id: string;
  label: string;
  Icon?: ComponentType<{ className?: string }>;
  badge?: ReactNode;
  placeholder?: boolean;
  onboardingAnchor?: string;
}
export interface ShellNavGroup {
  label?: string;
  items: ShellNavItem[];
}

export interface ProtoPageShellProps {
  variant: ShellVariant;
  guides: boolean;
  title: string;
  subtitle?: string;
  back: { label: string; onClick: () => void };
  actions?: ReactNode;
  groups: ShellNavGroup[];
  navLabel: string;
  activeId: string | null;
  onSelect: (id: string) => void;
  tier: Tier;
  /** C 的视图头标题 */
  viewTitle: string;
  footer?: ReactNode;
  children: ReactNode;
}

const NAV_W = "w-[200px] xl:w-[224px]";
const GUTTER_X = "px-6 xl:px-8";
const BG = "bg-[linear-gradient(180deg,var(--color-bg-grad-a),var(--color-bg-grad-b))]";

export function ProtoPageShell(props: ProtoPageShellProps) {
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  const Variant = props.variant === "B" ? VariantB : props.variant === "C" ? VariantC : VariantA;
  return (
    <FooterSlotContext.Provider value={slot}>
      <div className={cn("relative h-dvh overflow-hidden text-text", BG, props.guides && "proto-guides")}>
        <Variant {...props} slotRef={setSlot} />
      </div>
      <ShellProtoBar />
    </FooterSlotContext.Provider>
  );
}

type VariantProps = ProtoPageShellProps & { slotRef: (el: HTMLElement | null) => void };

// ---------------------------------------------------------------------------
// 共用小件（只是导航列表与返回按钮，不含布局）
// ---------------------------------------------------------------------------

function BackButton({ label, onClick, compact }: { label: string; onClick: () => void; compact?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1 rounded-md text-[13px] text-text-3 transition-colors hover:bg-accent hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        compact ? "-ml-1 px-1.5 py-1" : "px-2 py-1.5",
      )}
    >
      <ChevronLeft aria-hidden className="h-4 w-4" />
      {label}
    </button>
  );
}

function NavList({ groups, activeId, onSelect, label }: { groups: ShellNavGroup[]; activeId: string | null; onSelect: (id: string) => void; label: string }) {
  return (
    <nav aria-label={label} className="space-y-4">
      {groups.map((group, gi) => (
        <div key={group.label ?? gi}>
          {group.label && <div className="mb-1 px-2.5 text-[12px] text-text-3">{group.label}</div>}
          {group.items.map(({ id, label: itemLabel, Icon, badge, placeholder, onboardingAnchor }) => {
            const active = activeId === id;
            return (
              <button
                key={id}
                type="button"
                data-onboarding={onboardingAnchor}
                aria-current={active ? "page" : undefined}
                onClick={() => onSelect(id)}
                className={cn(
                  "mb-px flex h-8 w-full items-center gap-2 rounded-md px-2.5 text-left text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  active ? "bg-primary/14 text-text" : "text-text-2 hover:bg-accent hover:text-text",
                )}
              >
                {Icon && <Icon className={cn("h-4 w-4 shrink-0", active ? "text-primary" : "text-text-3")} />}
                <span className={cn("min-w-0 flex-1 truncate", placeholder && "italic text-text-3")}>{itemLabel}</span>
                {badge}
              </button>
            );
          })}
        </div>
      ))}
    </nav>
  );
}

/** 内容列：阅读与表单档限宽，wide 铺满，bleed 连内边距都交给区段。 */
function Column({ tier, children, className }: { tier: Tier; children: ReactNode; className?: string }) {
  if (tier === "bleed") {
    return (
      <div data-zone="column" data-tier={tier} className="flex h-full min-h-0 min-w-0 flex-1 flex-col">
        {children}
      </div>
    );
  }
  return (
    <div className={cn(GUTTER_X, "py-6", className)}>
      <div data-zone="column" data-tier={tier} className={cn("w-full min-w-0", TIER_MAX[tier])}>
        {children}
      </div>
    </div>
  );
}

/** 视图主体：bleed 档不滚动（区段内部各栏自己滚），其他档就是唯一滚动容器。 */
function Body({ tier, children }: { tier: Tier; children: ReactNode }) {
  return tier === "bleed" ? (
    <div data-zone="body" className="flex min-h-0 flex-1">
      <Column tier={tier}>{children}</Column>
    </div>
  ) : (
    <div data-zone="body" data-scroll-owner className="relative min-h-0 flex-1 overflow-y-auto [scrollbar-gutter:stable]">
      <Column tier={tier}>{children}</Column>
    </div>
  );
}

/** 保存栏插槽的内层：与内容列同宽、同起点，按钮因此落在表单右缘。 */
function FooterColumn({ tier, slotRef, footer }: { tier: Tier; slotRef: (el: HTMLElement | null) => void; footer?: ReactNode }) {
  return (
    <div
      ref={slotRef}
      data-zone="footer-column"
      data-footer-slot
      className={cn("flex w-full min-w-0 items-center", tier === "bleed" ? "" : TIER_MAX[tier])}
    >
      {footer}
    </div>
  );
}

const FOOTER_ROW = "shrink-0 border-t border-border bg-card has-[[data-footer-slot]:empty]:hidden";

// ---------------------------------------------------------------------------
// A — 顶栏贯通 · 左对齐
// 顶栏横跨全宽：返回按钮落在侧栏上方，标题与内容列左缘对齐。内容列紧贴侧栏、靠左限宽；
// 宽窗口多出的空白全在右侧。保存栏只横跨内容区（不压侧栏），内层与内容列等宽。
// ---------------------------------------------------------------------------

function VariantA(p: VariantProps) {
  return (
    <div className="flex h-full flex-col">
      <header data-zone="header" className="flex h-14 shrink-0 items-center border-b border-border">
        <div className={cn(NAV_W, "shrink-0 px-3")}>
          <BackButton {...p.back} />
        </div>
        <div className={cn("flex min-w-0 flex-1 items-baseline gap-3", GUTTER_X)}>
          <h1 className="truncate text-[17px] font-medium">{p.title}</h1>
          {p.subtitle && <span className="truncate text-[13px] text-text-3">{p.subtitle}</span>}
          {p.actions && <div className="ml-auto flex shrink-0 items-center gap-2 self-center">{p.actions}</div>}
        </div>
      </header>
      <div className="flex min-h-0 flex-1">
        <aside data-zone="nav" className={cn(NAV_W, "shrink-0 overflow-y-auto border-r border-border px-3 py-4")}>
          <NavList groups={p.groups} activeId={p.activeId} onSelect={p.onSelect} label={p.navLabel} />
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          <Body tier={p.tier}>{p.children}</Body>
          <div data-zone="footer" className={cn(FOOTER_ROW, p.tier === "bleed" ? "" : GUTTER_X, "py-3")}>
            <FooterColumn tier={p.tier} slotRef={p.slotRef} footer={p.footer} />
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// B — 居中组块
// 「侧栏 + 内容列」作为一个组块整体居中，顶栏与保存栏的内层都对齐到这个组块。
// 滚动容器是全宽的主体（滚动条贴窗口右缘，在两侧空白处滚轮也有效），侧栏在其中 sticky。
// 宽档与全出血档的组块不设上限，因此切到市场、使用记录、供应商时侧栏会跳到窗口左缘。
// ---------------------------------------------------------------------------

const FRAME_MAX: Record<Tier, string> = {
  reading: "max-w-[1056px] xl:max-w-[1088px]",
  form: "max-w-[1096px] xl:max-w-[1128px]",
  wide: "",
  bleed: "",
};

function VariantB(p: VariantProps) {
  const frame = cn("mx-auto w-full px-6", FRAME_MAX[p.tier]);
  return (
    <div className="flex h-full flex-col">
      <header data-zone="header" className="h-14 shrink-0 border-b border-border">
        <div className={cn(frame, "flex h-full items-center gap-3")}>
          <BackButton {...p.back} compact />
          <span aria-hidden className="h-4 w-px bg-border" />
          <h1 className="truncate text-[17px] font-medium">{p.title}</h1>
          {p.subtitle && <span className="truncate text-[13px] text-text-3">{p.subtitle}</span>}
          {p.actions && <div className="ml-auto flex shrink-0 items-center gap-2">{p.actions}</div>}
        </div>
      </header>

      {p.tier === "bleed" ? (
        <div className="flex min-h-0 flex-1">
          <div className={cn(frame, "flex min-h-0")}>
            <aside data-zone="nav" className={cn(NAV_W, "shrink-0 overflow-y-auto py-4 pr-3")}>
              <NavList groups={p.groups} activeId={p.activeId} onSelect={p.onSelect} label={p.navLabel} />
            </aside>
            <div data-zone="body" className="flex min-h-0 min-w-0 flex-1 border-x border-border">
              <Column tier="bleed">{p.children}</Column>
            </div>
          </div>
        </div>
      ) : (
        <div data-zone="body" data-scroll-owner className="relative min-h-0 flex-1 overflow-y-auto [scrollbar-gutter:stable]">
          <div className={cn(frame, "flex items-start")}>
            <aside data-zone="nav" className={cn(NAV_W, "sticky top-0 shrink-0 py-6 pr-3")}>
              <NavList groups={p.groups} activeId={p.activeId} onSelect={p.onSelect} label={p.navLabel} />
            </aside>
            <div className="min-w-0 flex-1 py-6 pl-6 xl:pl-8">
              <div data-zone="column" data-tier={p.tier} className={cn("w-full min-w-0", TIER_MAX[p.tier])}>
                {p.children}
              </div>
            </div>
          </div>
        </div>
      )}

      <div data-zone="footer" className={cn(FOOTER_ROW, "py-3")}>
        <div className={cn(frame, "flex")}>
          <div className={cn(NAV_W, "shrink-0")} />
          <div className={cn("min-w-0 flex-1", p.tier === "bleed" ? "" : "pl-6 xl:pl-8")}>
            <FooterColumn tier={p.tier} slotRef={p.slotRef} footer={p.footer} />
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// C — 侧栏头 · 无顶栏
// 没有页面级顶栏：返回、页面标题和全局动作都收进侧栏（侧栏通高）。右侧窗格自带视图头、
// 主体与保存栏三段，视图头显示当前分区名。内容列靠左限宽，与视图头标题、保存栏内层同一左缘。
// ---------------------------------------------------------------------------

function VariantC(p: VariantProps) {
  return (
    <div className="flex h-full">
      <aside data-zone="nav" className={cn(NAV_W, "flex shrink-0 flex-col border-r border-border bg-sidebar/60")}>
        <div className="shrink-0 px-3 pt-3 pb-3">
          <BackButton {...p.back} compact />
          <div className="mt-2 px-1">
            <div className="truncate text-[17px] font-medium">{p.title}</div>
            {p.subtitle && <div className="truncate text-[12px] text-text-3">{p.subtitle}</div>}
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-4">
          <NavList groups={p.groups} activeId={p.activeId} onSelect={p.onSelect} label={p.navLabel} />
        </div>
        {p.actions && <div className="shrink-0 border-t border-border px-3 py-2">{p.actions}</div>}
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <div data-zone="header" className={cn("flex h-14 shrink-0 items-center border-b border-border", GUTTER_X)}>
          <h2 className="truncate text-[15px] font-medium">{p.viewTitle}</h2>
        </div>
        <Body tier={p.tier}>{p.children}</Body>
        <div data-zone="footer" className={cn(FOOTER_ROW, p.tier === "bleed" ? "" : GUTTER_X, "py-3")}>
          <FooterColumn tier={p.tier} slotRef={p.slotRef} footer={p.footer} />
        </div>
      </div>
    </div>
  );
}
