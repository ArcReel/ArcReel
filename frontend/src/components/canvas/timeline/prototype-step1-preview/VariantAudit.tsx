/**
 * PROTOTYPE — throwaway（wayfinder #1481）。
 *
 * 变体 B「对照审计」：step1 契约 = 「原文拆得对不对、台词落位对不对」。
 * 双栏：左＝原文连续流（按 unit 分段，选中联动高亮），右＝unit 契约要素卡
 * （时长 / 台词清单 / 资产指认 / 镜头数）——**不展示**完整分镜描述文本。
 * 对第三问的回答：gate 与编辑器**并立**——gate 审契约，全文编辑去分镜编辑器。
 */
import { useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Lock, OctagonAlert, PenLine } from "lucide-react";
import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE, CARD_STYLE, GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";
import { DURATION_CHOICES, deriveUnit, type ProtoScenario, type ProtoUnit } from "./mock";
import { DerivedRefPills, DurationSelect } from "./shared";

function SourceSegment({
  unit,
  selected,
  anchorBroken,
  onSelect,
}: {
  unit: ProtoUnit;
  selected: boolean;
  anchorBroken: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      id={`proto-src-${unit.unitId}`}
      onClick={onSelect}
      className={`block w-full rounded-[8px] border-l-2 px-2.5 py-2 text-left transition-colors ${
        anchorBroken
          ? "border-red-400/70 bg-red-500/8"
          : selected
            ? "border-accent/70 bg-accent/8"
            : "border-hairline-soft hover:border-hairline-strong hover:bg-bg-grad-a/40"
      }`}
    >
      <span className="mb-1 flex items-center gap-1.5">
        <span className="rounded bg-bg-grad-a/70 px-1 py-px font-mono text-[10px] text-text-3">{unit.unitId}</span>
        {anchorBroken && (
          <span className="rounded bg-red-500/15 px-1 py-px text-[10px] text-red-300">非源文逐字子串</span>
        )}
      </span>
      <span className={`block text-[12px] leading-relaxed ${selected ? "text-text-2" : "text-text-3"}`}>
        {unit.sourceText}
      </span>
    </button>
  );
}

function ContractCard({
  unit,
  scenario,
  selected,
  onSelect,
}: {
  unit: ProtoUnit;
  scenario: ProtoScenario;
  selected: boolean;
  onSelect: () => void;
}) {
  const [duration, setDuration] = useState(unit.duration);
  const derived = useMemo(() => deriveUnit(unit.text), [unit.text]);
  const violations = scenario.violations.filter((v) => v.unitId === unit.unitId);
  const warnings = scenario.warnings.filter((w) => w.unitId === unit.unitId);

  return (
    <article
      id={`proto-audit-${unit.unitId}`}
      className={`rounded-[10px] border p-3 transition-colors ${
        violations.length > 0 ? "border-red-500/45" : selected ? "border-accent/50" : "border-hairline hover:border-hairline-strong"
      }`}
      style={CARD_STYLE}
    >
      <div className="mb-2 flex items-center gap-2">
        <button
          type="button"
          onClick={onSelect}
          aria-label={`定位 ${unit.unitId} 原文段`}
          className="rounded bg-bg-grad-a/70 px-1.5 py-0.5 font-mono text-[11px] text-text-2 hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          {unit.unitId}
        </button>
        <DurationSelect value={duration} choices={DURATION_CHOICES} onChange={setDuration} />
        <span className="font-mono text-[10.5px] text-text-4">{derived.shotCount} 镜头</span>
        <span className="flex-1" />
        <DerivedRefPills references={derived.references} />
      </div>

      {derived.utterances.length > 0 ? (
        <ul className="flex flex-col gap-1">
          {derived.utterances.map((u, i) => (
            <li key={i} className="flex items-start gap-1.5 text-[12px] leading-relaxed">
              {u.speaker ? (
                <span className="mt-px shrink-0 rounded bg-sky-500/15 px-1 py-px text-[10.5px] text-sky-300" translate="no">
                  {u.speaker}
                </span>
              ) : (
                <span className="mt-px shrink-0 rounded border border-hairline px-1 py-px text-[10px] text-text-4">画外音</span>
              )}
              <span className={u.speaker ? "text-text-2" : "italic text-text-3"}>{u.text}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[11px] text-text-4">本 unit 无台词</p>
      )}

      {violations.map((v) => (
        <p key={v.kind} className="mt-1.5 flex items-start gap-1 text-[11px] text-red-300">
          <OctagonAlert className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
          <span>
            <span className="mr-1 rounded bg-red-500/15 px-1 py-px text-[10px]">{v.kind}</span>
            {v.detail}
            <span className="block text-red-300/70">修复：{v.fix}</span>
          </span>
        </p>
      ))}
      {warnings.map((w) => (
        <p key={w.text} className="mt-1.5 flex items-start gap-1 text-[11px] text-amber-300/90">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
          {w.text}
        </p>
      ))}
    </article>
  );
}

export function VariantAudit({ scenario }: { scenario: ProtoScenario }) {
  const [selectedId, setSelectedId] = useState(scenario.units[0]?.unitId ?? "");
  const quarantined = scenario.quarantined;
  const brokenAnchors = new Set(
    scenario.violations.filter((v) => v.kind === "原文锚失配").map((v) => v.unitId),
  );

  const select = (unitId: string, scrollTo: string) => {
    setSelectedId(unitId);
    document.getElementById(scrollTo)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };

  return (
    <div className="flex flex-col gap-3">
      <header
        className="sticky top-0 z-10 flex items-center justify-between gap-3 rounded-[10px] border border-hairline px-3.5 py-2.5 backdrop-blur-md"
        style={CARD_STYLE}
      >
        <div className="flex items-center gap-2">
          {quarantined ? <OctagonAlert className="h-4 w-4 text-red-400" /> : <CheckCircle2 className="h-4 w-4 text-amber-400" />}
          <div className="flex flex-col">
            <span className="text-[12.5px] font-medium text-text">
              {quarantined ? "草稿隔离中 — 拆分未通过校验" : "第 3 集拆分待确认"}
            </span>
            <span className="text-[11px] text-text-4">
              原文 {scenario.units.length} 段 · 台词与时长构成生成契约，逐段对照审核
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {quarantined && (
            <button type="button" className={GHOST_BTN_CLS}>
              让助手修复
            </button>
          )}
          <button
            type="button"
            disabled={quarantined}
            className={ACCENT_BTN_CLS}
            style={ACCENT_BUTTON_STYLE}
            title={quarantined ? "存在阻断违约，修复晋升后才能确认" : undefined}
          >
            {quarantined ? <Lock className="h-3.5 w-3.5" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
            确认拆分，继续生成
          </button>
        </div>
      </header>

      <div className="grid grid-cols-[5fr_6fr] gap-3">
        <section aria-label="小说原文" className="flex flex-col gap-1.5">
          <h3 className="px-1 font-mono text-[10.5px] tracking-[0.08em] text-text-4">原文（按 unit 分段）</h3>
          {scenario.units.map((unit) => (
            <SourceSegment
              key={unit.unitId}
              unit={unit}
              selected={selectedId === unit.unitId}
              anchorBroken={brokenAnchors.has(unit.unitId)}
              onSelect={() => select(unit.unitId, `proto-audit-${unit.unitId}`)}
            />
          ))}
        </section>

        <section aria-label="拆分契约" className="flex flex-col gap-2">
          <h3 className="px-1 font-mono text-[10.5px] tracking-[0.08em] text-text-4">拆分契约（时长 · 台词 · 资产）</h3>
          {scenario.units.map((unit) => (
            <ContractCard
              key={unit.unitId}
              unit={unit}
              scenario={scenario}
              selected={selectedId === unit.unitId}
              onSelect={() => select(unit.unitId, `proto-src-${unit.unitId}`)}
            />
          ))}
          <p className="flex items-center gap-1.5 px-1 text-[10.5px] text-text-4">
            <PenLine className="h-3 w-3" aria-hidden="true" />
            完整分镜描述在「单元」页编辑，确认后生成时使用
          </p>
        </section>
      </div>
    </div>
  );
}
