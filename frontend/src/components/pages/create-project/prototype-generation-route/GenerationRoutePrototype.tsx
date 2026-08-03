// ─── PROTOTYPE（用后即弃，勿并入生产）─────────────────────────────────────────
// 创建向导「生成方式」引导卡的三个结构变体，挂在 Step 1 原三值选择器的位置，
// 通过 ?variant=A|B|C 切换（仅 DEV 构建）。
//   A 契约卡   — 双竖卡并排，宫格开关内嵌为卡 1 底部子面板，锁定提示为底部脚注
//   B 台账行   — 全宽横行堆叠，选中后手风琴展开宫格子行，锁定提示为标题右侧徽章
//   C 对比双联 — 单框中缝分屏，宫格开关为外部「装配条」，锁定提示为琥珀图章
// 文案为定稿文案，原型阶段不改写；实现阶段录入 i18n（zh/en/vi）。
// 状态仅存于本组件内存（不回写向导表单、不发请求）。
import { useState } from "react";
import { ArrowRight, Box, Image as ImageIcon, LayoutGrid, Lock, Play, Trees, User } from "lucide-react";
import { FieldLabel } from "@/components/ui/FieldLabel";
import { PrototypeSwitcher, useVariantParam } from "./PrototypeSwitcher";

// ─── 定稿文案 ────────────────────────────────────────────────────────────────
const COPY = {
  sectionLabel: "生成方式",
  storyboard: {
    title: "分镜图生视频",
    desc: "先为每个场景生成分镜图，确认画面后再生成视频",
    tag: "I2V",
  },
  reference: {
    title: "参考生视频",
    desc: "跳过分镜图，直接用角色、场景、道具图作为参考生成视频",
    tag: "R2V",
  },
  grid: {
    title: "分镜板（宫格）生视频",
    desc: "多个场景合成一张宫格大图一次生成，画风和角色更一致，创建后可随时切换",
  },
  lock: "生成方式创建后不可更改",
} as const;

const VARIANTS = { A: "契约卡", B: "台账行", C: "对比双联" } as const;
type VariantKey = keyof typeof VARIANTS;

type Route = "storyboard" | "reference_video" | null;

interface RouteState {
  route: Route;
  grid: boolean;
}

interface VariantProps {
  state: RouteState;
  onChange: (next: RouteState) => void;
}

// ─── 共享小件 ────────────────────────────────────────────────────────────────

/** 契约图示：单张分镜帧（胶片框）→ 视频。分镜路线的输入契约。 */
function StoryboardDiagram({ active }: { active: boolean }) {
  const frameCls = active ? "border-accent/50 bg-accent-dim" : "border-hairline bg-bg/60";
  return (
    <div aria-hidden className="flex items-center gap-2.5">
      <div className={`relative h-12 w-[38px] rounded-[4px] border ${frameCls} transition-colors`}>
        {/* sprocket 孔 — 呼应 StepIndicator 的胶片语汇 */}
        {[0, 1, 2].map((i) => (
          <span key={i}>
            <span
              className="absolute left-[3px] h-[3px] w-[3px] rounded-[1px] bg-hairline-strong"
              style={{ top: 8 + i * 14 }}
            />
            <span
              className="absolute right-[3px] h-[3px] w-[3px] rounded-[1px] bg-hairline-strong"
              style={{ top: 8 + i * 14 }}
            />
          </span>
        ))}
        <ImageIcon
          className={`absolute left-1/2 top-1/2 h-4 w-4 -translate-x-1/2 -translate-y-1/2 ${active ? "text-accent-2" : "text-text-4"}`}
        />
      </div>
      <ArrowRight className="h-3.5 w-3.5 shrink-0 text-text-4" />
      <PlayFrame active={active} />
    </div>
  );
}

/** 契约图示：角色/场景/道具参考图集合 → 视频。参考路线的输入契约。 */
function ReferenceDiagram({ active }: { active: boolean }) {
  const chip = (icon: React.ReactNode, i: number) => (
    <span
      key={i}
      className={`grid h-9 w-9 place-items-center rounded-[6px] border transition-colors ${
        active ? "border-accent/50 bg-accent-dim" : "border-hairline bg-bg/60"
      }`}
      style={{ transform: `rotate(${(i - 1) * 5}deg) translateY(${i === 1 ? -2 : 2}px)` }}
    >
      {icon}
    </span>
  );
  const iconCls = `h-4 w-4 ${active ? "text-accent-2" : "text-text-4"}`;
  return (
    <div aria-hidden className="flex items-center gap-2.5">
      <div className="flex -space-x-2.5">
        {chip(<User className={iconCls} />, 0)}
        {chip(<Trees className={iconCls} />, 1)}
        {chip(<Box className={iconCls} />, 2)}
      </div>
      <ArrowRight className="h-3.5 w-3.5 shrink-0 text-text-4" />
      <PlayFrame active={active} />
    </div>
  );
}

function PlayFrame({ active }: { active: boolean }) {
  return (
    <span
      className={`grid h-12 w-[38px] place-items-center rounded-[4px] border border-dashed transition-colors ${
        active ? "border-accent/50" : "border-hairline"
      }`}
    >
      <Play
        className={`h-4 w-4 ${active ? "fill-accent-2 text-accent-2" : "fill-text-4 text-text-4"}`}
      />
    </span>
  );
}

/** 极简 pill 开关（仓库尚无 Switch 组件，原型内自带）。 */
function PillSwitch({
  checked,
  disabled,
  onToggle,
  label,
}: {
  checked: boolean;
  disabled?: boolean;
  onToggle: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={(e) => {
        e.stopPropagation();
        onToggle();
      }}
      className={`relative h-[18px] w-8 shrink-0 rounded-full border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-40 ${
        checked ? "border-accent/60 bg-accent-soft" : "border-hairline bg-bg-grad-a/70"
      }`}
    >
      <span
        className={`absolute top-1/2 h-3 w-3 -translate-y-1/2 rounded-full transition-all ${
          checked ? "left-[15px] bg-accent-2" : "left-[2px] bg-text-4"
        }`}
      />
    </button>
  );
}

const gridToggle = (state: RouteState, onChange: VariantProps["onChange"]) => () =>
  onChange({ ...state, grid: !state.grid });

const pickRoute = (state: RouteState, onChange: VariantProps["onChange"], route: Route) => () =>
  onChange({ route, grid: route === "storyboard" ? state.grid : false });

// ─── Variant A：契约卡 ───────────────────────────────────────────────────────
function VariantA({ state, onChange }: VariantProps) {
  const cardCls = (selected: boolean) =>
    `group flex flex-1 cursor-pointer flex-col rounded-[10px] border p-4 text-left transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-accent ${
      selected
        ? "border-accent/55 bg-accent-dim shadow-[inset_0_1px_0_oklch(1_0_0_/_0.05),0_0_26px_-10px_var(--color-accent-glow)]"
        : "border-hairline-soft bg-bg-grad-a/40 hover:border-hairline"
    }`;
  const sb = state.route === "storyboard";
  const rv = state.route === "reference_video";
  return (
    <div className="space-y-2.5">
      <div className="flex gap-3" role="radiogroup" aria-label={COPY.sectionLabel}>
        {/* 卡 1 — 分镜图生视频，内嵌宫格开关 */}
        <label className={cardCls(sb)}>
          <input
            type="radio"
            name="proto-route-a"
            checked={sb}
            onChange={pickRoute(state, onChange, "storyboard")}
            className="sr-only"
          />
          <span
            className={`font-mono text-[9.5px] font-bold uppercase tracking-[0.16em] ${sb ? "text-accent-2" : "text-text-4"}`}
          >
            {COPY.storyboard.tag}
          </span>
          <div className="mt-3 flex justify-center">
            <StoryboardDiagram active={sb} />
          </div>
          <div className="mt-3 text-center text-[13.5px] font-semibold text-text">
            {COPY.storyboard.title}
          </div>
          <p className="mt-1 flex-1 text-center text-[11.5px] leading-[1.55] text-text-3">
            {COPY.storyboard.desc}
          </p>
          {/* 内嵌子面板：宫格开关 */}
          <div
            className={`mt-3 rounded-[7px] border p-2.5 transition-opacity ${
              sb ? "border-hairline-soft bg-bg/40" : "border-hairline-soft bg-bg/25 opacity-45"
            }`}
          >
            <div className="flex items-center gap-2">
              <LayoutGrid className={`h-3.5 w-3.5 shrink-0 ${state.grid && sb ? "text-accent-2" : "text-text-4"}`} />
              <span className="flex-1 text-[11.5px] font-medium text-text-2">{COPY.grid.title}</span>
              <PillSwitch
                checked={sb && state.grid}
                disabled={!sb}
                onToggle={gridToggle(state, onChange)}
                label={COPY.grid.title}
              />
            </div>
            <p className="mt-1.5 pl-[22px] text-[10.5px] leading-[1.5] text-text-4">{COPY.grid.desc}</p>
          </div>
        </label>

        {/* 卡 2 — 参考生视频 */}
        <label className={cardCls(rv)}>
          <input
            type="radio"
            name="proto-route-a"
            checked={rv}
            onChange={pickRoute(state, onChange, "reference_video")}
            className="sr-only"
          />
          <span
            className={`font-mono text-[9.5px] font-bold uppercase tracking-[0.16em] ${rv ? "text-accent-2" : "text-text-4"}`}
          >
            {COPY.reference.tag}
          </span>
          <div className="mt-3 flex justify-center">
            <ReferenceDiagram active={rv} />
          </div>
          <div className="mt-3 text-center text-[13.5px] font-semibold text-text">
            {COPY.reference.title}
          </div>
          <p className="mt-1 flex-1 text-center text-[11.5px] leading-[1.55] text-text-3">
            {COPY.reference.desc}
          </p>
        </label>
      </div>
      {/* 锁定提示 — 底部脚注 */}
      <p className="flex items-center justify-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.1em] text-text-3">
        <Lock className="h-3 w-3 text-warm" aria-hidden />
        {COPY.lock}
      </p>
    </div>
  );
}

// ─── Variant B：台账行 ───────────────────────────────────────────────────────
function VariantB({ state, onChange }: VariantProps) {
  const rowCls = (selected: boolean) =>
    `flex w-full cursor-pointer items-start gap-3 rounded-[9px] border px-3.5 py-3 text-left transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-accent ${
      selected
        ? "border-accent/55 bg-accent-dim shadow-[0_0_22px_-10px_var(--color-accent-glow)]"
        : "border-hairline-soft bg-bg-grad-a/40 hover:border-hairline"
    }`;
  const marker = (selected: boolean) => (
    <span
      aria-hidden
      className={`mt-[3px] grid h-4 w-4 shrink-0 place-items-center rounded-full border transition-colors ${
        selected ? "border-accent/70" : "border-hairline-strong"
      }`}
    >
      {selected ? <span className="h-2 w-2 rounded-full bg-accent-2" /> : null}
    </span>
  );
  const sb = state.route === "storyboard";
  const rv = state.route === "reference_video";
  return (
    <div className="space-y-2">
      {/* 标题行内锁定徽章 */}
      <div className="flex items-center justify-between">
        <span className="text-[12px] font-medium text-text-2">{COPY.sectionLabel}</span>
        <span className="inline-flex items-center gap-1 rounded-[5px] border border-warm-ring bg-warm-tint-faint px-1.5 py-[3px] font-mono text-[9px] font-bold uppercase tracking-[0.1em] text-warm">
          <Lock className="h-2.5 w-2.5" aria-hidden />
          {COPY.lock}
        </span>
      </div>

      <div role="radiogroup" aria-label={COPY.sectionLabel} className="space-y-2">
        {/* 行 1 — 分镜图生视频 + 手风琴宫格子行 */}
        <label className={rowCls(sb)}>
          <input
            type="radio"
            name="proto-route-b"
            checked={sb}
            onChange={pickRoute(state, onChange, "storyboard")}
            className="sr-only"
          />
          {marker(sb)}
          <span className="min-w-0 flex-1">
            <span className="flex items-baseline gap-2">
              <span className="text-[13px] font-semibold text-text">{COPY.storyboard.title}</span>
              <span className={`font-mono text-[9px] font-bold uppercase tracking-[0.14em] ${sb ? "text-accent-2" : "text-text-4"}`}>
                {COPY.storyboard.tag}
              </span>
            </span>
            <span className="mt-0.5 block text-[11.5px] leading-[1.5] text-text-3">
              {COPY.storyboard.desc}
            </span>
            {/* 手风琴：选中后展开宫格子行 */}
            {sb ? (
              <span className="mt-2.5 flex items-start gap-2.5 border-l-2 border-accent/40 pl-3">
                <LayoutGrid className={`mt-[2px] h-3.5 w-3.5 shrink-0 ${state.grid ? "text-accent-2" : "text-text-4"}`} />
                <span className="min-w-0 flex-1">
                  <span className="block text-[11.5px] font-medium text-text-2">{COPY.grid.title}</span>
                  <span className="mt-0.5 block text-[10.5px] leading-[1.5] text-text-4">{COPY.grid.desc}</span>
                </span>
                <PillSwitch checked={state.grid} onToggle={gridToggle(state, onChange)} label={COPY.grid.title} />
              </span>
            ) : null}
          </span>
        </label>

        {/* 行 2 — 参考生视频 */}
        <label className={rowCls(rv)}>
          <input
            type="radio"
            name="proto-route-b"
            checked={rv}
            onChange={pickRoute(state, onChange, "reference_video")}
            className="sr-only"
          />
          {marker(rv)}
          <span className="min-w-0 flex-1">
            <span className="flex items-baseline gap-2">
              <span className="text-[13px] font-semibold text-text">{COPY.reference.title}</span>
              <span className={`font-mono text-[9px] font-bold uppercase tracking-[0.14em] ${rv ? "text-accent-2" : "text-text-4"}`}>
                {COPY.reference.tag}
              </span>
            </span>
            <span className="mt-0.5 block text-[11.5px] leading-[1.5] text-text-3">
              {COPY.reference.desc}
            </span>
          </span>
        </label>
      </div>
    </div>
  );
}

// ─── Variant C：对比双联 ─────────────────────────────────────────────────────
function VariantC({ state, onChange }: VariantProps) {
  const sb = state.route === "storyboard";
  const rv = state.route === "reference_video";
  const halfCls = (selected: boolean) =>
    `relative flex cursor-pointer flex-col items-center gap-2.5 px-4 py-5 text-center transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-accent ${
      selected ? "bg-accent-dim" : "hover:bg-bg-grad-a/60"
    }`;
  return (
    <div className="space-y-2.5">
      {/* 标题行 — 与其他选项组同款 FieldLabel，锁定徽章紧挨标题 */}
      <div className="flex items-center gap-2">
        <FieldLabel className="mb-0">{COPY.sectionLabel}</FieldLabel>
        <span className="inline-flex items-center gap-1 rounded-[5px] border border-warm-ring bg-warm-tint-faint px-1.5 py-[3px] font-mono text-[9px] font-bold uppercase tracking-[0.1em] text-warm">
          <Lock className="h-2.5 w-2.5" aria-hidden />
          {COPY.lock}
        </span>
      </div>
      <div
        className="relative grid grid-cols-2 overflow-hidden rounded-[12px] border border-hairline"
        role="radiogroup"
        aria-label={COPY.sectionLabel}
        style={{
          background: "linear-gradient(180deg, oklch(0.19 0.011 268 / 0.6), oklch(0.15 0.010 262 / 0.6))",
        }}
      >
        {/* 中缝 */}
        <div aria-hidden className="pointer-events-none absolute inset-y-0 left-1/2 w-px bg-hairline" />
        {/* 选中侧内描边 */}
        {state.route ? (
          <div
            aria-hidden
            className="pointer-events-none absolute inset-y-0 w-1/2 border-2 border-accent/45 transition-[left] duration-300"
            style={{
              left: sb ? 0 : "50%",
              borderRadius: sb ? "12px 0 0 12px" : "0 12px 12px 0",
              boxShadow: "inset 0 0 30px -18px var(--color-accent-glow)",
            }}
          />
        ) : null}

        <label className={halfCls(sb)}>
          <input
            type="radio"
            name="proto-route-c"
            checked={sb}
            onChange={pickRoute(state, onChange, "storyboard")}
            className="sr-only"
          />
          <span className={`font-mono text-[9.5px] font-bold uppercase tracking-[0.16em] ${sb ? "text-accent-2" : "text-text-4"}`}>
            {COPY.storyboard.tag}
          </span>
          <StoryboardDiagram active={sb} />
          <span className="text-[14.5px] font-semibold text-text">{COPY.storyboard.title}</span>
          <span className="text-[11.5px] leading-[1.55] text-text-3">{COPY.storyboard.desc}</span>
        </label>

        <label className={halfCls(rv)}>
          <input
            type="radio"
            name="proto-route-c"
            checked={rv}
            onChange={pickRoute(state, onChange, "reference_video")}
            className="sr-only"
          />
          <span className={`font-mono text-[9.5px] font-bold uppercase tracking-[0.16em] ${rv ? "text-accent-2" : "text-text-4"}`}>
            {COPY.reference.tag}
          </span>
          <ReferenceDiagram active={rv} />
          <span className="text-[14.5px] font-semibold text-text">{COPY.reference.title}</span>
          <span className="text-[11.5px] leading-[1.55] text-text-3">{COPY.reference.desc}</span>
        </label>
      </div>

      {/* 装配条 — 仅分镜路线出现，强调宫格是路线内选项而非第三条路线 */}
      {sb ? (
        <div className="flex items-start gap-2.5 rounded-[9px] border border-hairline-soft bg-bg-grad-a/50 px-3.5 py-2.5 motion-safe:animate-[proto-slide-in_0.25s_ease-out]">
          <LayoutGrid className={`mt-[2px] h-3.5 w-3.5 shrink-0 ${state.grid ? "text-accent-2" : "text-text-4"}`} />
          <div className="min-w-0 flex-1">
            <div className="text-[11.5px] font-medium text-text-2">{COPY.grid.title}</div>
            <div className="mt-0.5 text-[10.5px] leading-[1.5] text-text-4">{COPY.grid.desc}</div>
          </div>
          <PillSwitch checked={state.grid} onToggle={gridToggle(state, onChange)} label={COPY.grid.title} />
        </div>
      ) : null}

      <style>{`@keyframes proto-slide-in { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: none; } }`}</style>
    </div>
  );
}

// ─── 挂载壳 ──────────────────────────────────────────────────────────────────

export function useGenerationRouteVariant(): VariantKey | null {
  return useVariantParam(Object.keys(VARIANTS)) as VariantKey | null;
}

export function GenerationRoutePrototype({ variant }: { variant: VariantKey }) {
  // 状态提升到壳层：切换变体时保留已选路线/开关，便于横向对比
  const [state, setState] = useState<RouteState>({ route: null, grid: false });
  const Body = variant === "A" ? VariantA : variant === "B" ? VariantB : VariantC;
  return (
    <div data-prototype="generation-route">
      <Body state={state} onChange={setState} />
      <PrototypeSwitcher
        variants={VARIANTS}
        current={variant}
        stateLabel={`route=${state.route ?? "∅"} · grid=${state.grid ? "on" : "off"}`}
      />
    </div>
  );
}
