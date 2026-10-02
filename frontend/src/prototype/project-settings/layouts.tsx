// PROTOTYPE — 项目设置页布局（#2971）的三个变体。
// 「三个变体，同一路由 /app/projects/:name/settings，?variant=A|B|C 切换。」
//
// 三个变体共用同一份表单状态、同一条保存栏（风格已并入统一保存），分歧在：
//   A 独立外壳 · 侧栏分页：一组一屏，模型页带覆盖说明条，风格用 Dialog 更换。
//   B 独立外壳 · 锚点长页：全部表单一页滚完，侧栏做锚点；宽窗口右侧加「生效配置」摘要；
//     记忆与 Agent 配置移出项目设置；风格网格在页内展开。
//   C 嵌进工作区：不离开工作区外壳（顶栏、资产侧栏、Agent 面板都在），画布区内用横向分页。

import { useEffect, useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ProtoPageShell } from "@/prototype/page-shell/ProtoPageShell";
import type { ShellNavItem } from "@/prototype/page-shell/ProtoPageShell";
import { FormSection, GroupHeader, SummaryPanel, UnsavedGuardDialog, type PsGroup, type PsSummaryRow } from "./parts";
import { PsProtoBar } from "./PsProtoBar";
import { usePsParams } from "./ps-variant";

export interface PsLayoutProps {
  title: string;
  subtitle: string;
  back: { label: string; onClick: () => void };
  groups: PsGroup[];
  saveBar: ReactNode;
  isDirty: boolean;
  onSave: () => Promise<boolean>;
  onDiscard: () => void;
  onResetOverrides: () => void;
  summary: PsSummaryRow[];
  /** 页面自带的对话框（Agent 配置重置确认等） */
  dialogs: ReactNode;
}

function NavBadge({ group }: { group: PsGroup }) {
  if (!group.overrides && !group.dirty) return null;
  return (
    <span className="flex shrink-0 items-center gap-1.5">
      {!!group.overrides && <span className="text-[11px] tabular-nums text-text-3">{group.overrides} 项覆盖</span>}
      {group.dirty && <span role="img" aria-label="有未保存修改" className="h-1.5 w-1.5 rounded-full bg-warm" />}
    </span>
  );
}

/** 一组一屏时的页面：组名作页头；只有一个分区的组不再重复分区标题。 */
function GroupPage({ group, onResetOverrides }: { group: PsGroup; onResetOverrides: () => void }) {
  return (
    <div data-ps-column>
      <GroupHeader group={group} onResetOverrides={onResetOverrides} />
      {group.items.length === 1 ? (
        <div>
          {group.items[0].description && (
            <p className="-mt-3 mb-5 max-w-[40em] text-[13px] leading-[1.6] text-text-3">{group.items[0].description}</p>
          )}
          {group.items[0].node}
        </div>
      ) : (
        <div className="space-y-6">
          {group.items.map((item) => (
            <FormSection key={item.id} item={item} level={3} />
          ))}
        </div>
      )}
    </div>
  );
}

/** 分页切换：从表单页切到记忆或 Agent 配置页（不归保存栏管）时，有草稿就拦截。 */
function useGuardedTabs(p: PsLayoutProps) {
  const { tab, setTab } = usePsParams();
  const active = p.groups.find((g) => g.id === tab) ?? p.groups[0];
  const [pending, setPending] = useState<string | null>(null);
  const request = (id: string) => {
    const target = p.groups.find((g) => g.id === id);
    if (p.isDirty && target?.agent && !active.agent) {
      setPending(id);
      return;
    }
    setTab(id);
  };
  const guard = (
    <UnsavedGuardDialog
      open={pending !== null}
      onStay={() => setPending(null)}
      onDiscard={() => {
        p.onDiscard();
        if (pending) setTab(pending);
        setPending(null);
      }}
      onSave={async () => {
        const ok = await p.onSave();
        if (ok && pending) {
          setTab(pending);
          setPending(null);
        }
        return ok;
      }}
    />
  );
  return { active, request, guard };
}

// ---------------------------------------------------------------------------
// A — 独立外壳 · 侧栏分页
// ---------------------------------------------------------------------------

export function LayoutA(p: PsLayoutProps) {
  const { guides } = usePsParams();
  const { active, request, guard } = useGuardedTabs(p);
  const toNav = (g: PsGroup): ShellNavItem => ({ id: g.id, label: g.label, badge: <NavBadge group={g} /> });
  return (
    <ProtoPageShell
      variant="D"
      guides={guides}
      title={p.title}
      subtitle={p.subtitle}
      back={p.back}
      groups={[
        { label: "项目", items: p.groups.filter((g) => !g.agent).map(toNav) },
        { label: "Agent", items: p.groups.filter((g) => g.agent).map(toNav) },
      ]}
      navLabel={p.title}
      activeId={active.id}
      onSelect={request}
      tier="form"
      viewTitle={active.label}
      footer={active.agent ? undefined : p.saveBar}
      hideProtoBar
    >
      <GroupPage key={active.id} group={active} onResetOverrides={p.onResetOverrides} />
      {guard}
      {p.dialogs}
      <PsProtoBar />
    </ProtoPageShell>
  );
}

// ---------------------------------------------------------------------------
// B — 独立外壳 · 锚点长页 + 生效摘要
// ---------------------------------------------------------------------------

export function LayoutB(p: PsLayoutProps) {
  const { guides } = usePsParams();
  const formGroups = p.groups.filter((g) => !g.agent);
  const [activeId, setActiveId] = useState(formGroups[0]?.id ?? null);

  // 锚点高亮：滚动容器顶端以下 80px 内最后一个越过的组
  useEffect(() => {
    const owner = document.querySelector<HTMLElement>("[data-ps-scroll]");
    if (!owner) return;
    const onScroll = () => {
      const top = owner.getBoundingClientRect().top + 80;
      let current = formGroups[0]?.id ?? null;
      for (const g of formGroups) {
        const el = document.getElementById(`ps-g-${g.id}`);
        if (el && el.getBoundingClientRect().top <= top) current = g.id;
      }
      if (owner.scrollTop + owner.clientHeight >= owner.scrollHeight - 2) current = formGroups.at(-1)?.id ?? current;
      setActiveId(current);
    };
    owner.addEventListener("scroll", onScroll, { passive: true });
    return () => owner.removeEventListener("scroll", onScroll);
  });

  const jump = (id: string) => {
    setActiveId(id);
    document.getElementById(`ps-g-${id}`)?.scrollIntoView({ block: "start" });
  };

  return (
    <ProtoPageShell
      variant="D"
      guides={guides}
      title={p.title}
      subtitle={p.subtitle}
      back={p.back}
      groups={[{ items: formGroups.map((g) => ({ id: g.id, label: g.label, badge: <NavBadge group={g} /> })) }]}
      navLabel={p.title}
      activeId={activeId}
      onSelect={jump}
      tier="form"
      viewTitle={p.title}
      footer={p.saveBar}
      hideProtoBar
    >
      <div data-ps-column className="relative">
        {formGroups.map((g) => (
          <div key={g.id} id={`ps-g-${g.id}`} className="scroll-mt-6 pb-12 last:pb-0">
            <div className="mb-5 border-b border-border pb-2">
              <h2 className="text-[17px] font-medium text-text">{g.label}</h2>
            </div>
            <div className="space-y-6">
              {g.items.map((item) => (
                <FormSection key={item.id} item={item} level={3} />
              ))}
            </div>
          </div>
        ))}
        <p className="mt-12 rounded-lg border border-dashed border-border px-4 py-3 text-[13px] leading-[1.6] text-text-3">
          项目记忆与 Agent 配置不在项目设置里：在工作区 Agent 面板的「记忆」中查看和编辑。（原型示意，入口未实现）
        </p>
        {/* 摘要栏挂在内容列右侧的空白里，不占内容列宽度；容器不够宽时不显示 */}
        <aside className="absolute top-0 bottom-0 left-[calc(100%+40px)] hidden w-[264px] @min-[1140px]/body:block">
          <div className="sticky top-0">
            <SummaryPanel rows={p.summary} onJump={jump} />
          </div>
        </aside>
      </div>
      {p.dialogs}
      <PsProtoBar />
    </ProtoPageShell>
  );
}

// ---------------------------------------------------------------------------
// C — 嵌进工作区 · 画布内分页（由 StudioCanvasRouter 的 /settings 路由渲染）
// ---------------------------------------------------------------------------

export function LayoutC(p: PsLayoutProps) {
  const { guides } = usePsParams();
  const { active, request, guard } = useGuardedTabs(p);
  return (
    <div className={cn("relative flex h-full min-h-0 flex-col", guides && "proto-guides")}>
      <div className="shrink-0 border-b border-border px-6 pt-4">
        <div className="flex items-baseline gap-3">
          <h1 className="text-[17px] font-medium text-text">{p.title}</h1>
        </div>
        <Tabs value={active.id} onValueChange={(v) => request(String(v))} className="mt-2">
          <TabsList variant="line" className="-mb-px h-9">
            {p.groups.map((g) => (
              <TabsTrigger key={g.id} value={g.id} className="px-2.5">
                {g.label}
                <NavBadge group={g} />
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>
      <div data-ps-scroll className="relative min-h-0 flex-1 overflow-y-auto [scrollbar-gutter:stable]">
        <div className="px-6 py-6">
          <div className="max-w-[760px]">
            <GroupPage key={active.id} group={active} onResetOverrides={p.onResetOverrides} />
          </div>
        </div>
      </div>
      {!active.agent && (
        <div className="shrink-0 border-t border-border bg-card px-6 py-3">
          <div className="flex max-w-[760px]">{p.saveBar}</div>
        </div>
      )}
      {guard}
      {p.dialogs}
      <PsProtoBar />
    </div>
  );
}
