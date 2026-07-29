/**
 * PROTOTYPE — throwaway（wayfinder #1481）。
 *
 * 变体 C「报告中枢」：校验报告置顶为第一公民，违约条目携「让助手修复」流转入口
 * （对应「隔离草稿 + 结构化违约报告 + agent 在场修复 + 晋升」机制）；
 * units 收成紧凑手风琴，只读——内容细读靠展开，审核效率优先。
 */
import { useMemo, useState } from "react";
import { AlertTriangle, Bot, CheckCircle2, ChevronDown, Lock, OctagonAlert } from "lucide-react";
import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE, CARD_STYLE } from "@/components/ui/darkroom-tokens";
import { DURATION_CHOICES, deriveUnit, type ProtoScenario, type ProtoUnit } from "./mock";
import { DerivedRefPills, DurationSelect, LineView } from "./shared";

function ReportPanel({ scenario, onLocate }: { scenario: ProtoScenario; onLocate: (unitId: string) => void }) {
  const [sent, setSent] = useState(false);
  const { violations, warnings, quarantined } = scenario;

  return (
    <section
      aria-label="校验报告"
      className={`rounded-[10px] border p-3.5 ${quarantined ? "border-red-500/40" : "border-hairline"}`}
      style={CARD_STYLE}
    >
      <div className="mb-2.5 flex items-center gap-2">
        {quarantined ? (
          <OctagonAlert className="h-4 w-4 text-red-400" aria-hidden="true" />
        ) : (
          <CheckCircle2 className="h-4 w-4 text-emerald-400" aria-hidden="true" />
        )}
        <span className="text-[12.5px] font-medium text-text">
          {quarantined ? `校验未通过 — ${violations.length} 条违约，产物停在隔离草稿` : "结构校验通过"}
        </span>
        <span className="flex-1" />
        {quarantined && (
          <button
            type="button"
            onClick={() => setSent(true)}
            disabled={sent}
            className={`inline-flex items-center gap-1.5 rounded-[8px] border px-3 py-1.5 text-[12px] transition-colors ${
              sent
                ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                : "border-red-500/40 bg-red-500/10 text-red-300 hover:bg-red-500/15"
            }`}
          >
            <Bot className="h-3.5 w-3.5" aria-hidden="true" />
            {sent ? "报告已发送给助手（示意）" : "让助手按报告修复"}
          </button>
        )}
      </div>

      {violations.length > 0 && (
        <ol className="flex flex-col gap-1.5">
          {violations.map((v, i) => (
            <li key={i} className="rounded-[8px] border border-red-500/25 bg-red-500/6 p-2">
              <div className="flex items-start gap-2">
                <span className="rounded bg-red-500/15 px-1.5 py-0.5 text-[10px] text-red-300">{v.kind}</span>
                <div className="min-w-0 flex-1">
                  <p className="text-[11.5px] leading-relaxed text-red-200/90">{v.detail}</p>
                  <p className="text-[11px] text-red-300/70">修复：{v.fix}</p>
                </div>
                <button
                  type="button"
                  onClick={() => onLocate(v.unitId)}
                  className="shrink-0 rounded border border-hairline px-1.5 py-0.5 font-mono text-[10.5px] text-text-3 hover:text-text"
                >
                  {v.unitId}
                </button>
              </div>
            </li>
          ))}
        </ol>
      )}

      {warnings.length > 0 && (
        <div className={violations.length > 0 ? "mt-2" : ""}>
          <p className="mb-1 font-mono text-[10px] tracking-[0.08em] text-text-4">提醒（不阻断确认）</p>
          <ul className="flex flex-col gap-1">
            {warnings.map((w, i) => (
              <li key={i} className="flex items-start gap-1.5 text-[11px] text-amber-300/90">
                <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
                <span className="min-w-0 flex-1">{w.text}</span>
                <button
                  type="button"
                  onClick={() => onLocate(w.unitId)}
                  className="shrink-0 rounded border border-hairline px-1.5 py-0.5 font-mono text-[10px] text-text-4 hover:text-text"
                >
                  {w.unitId}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function UnitRow({
  unit,
  open,
  problemCount,
  warnCount,
  onToggle,
}: {
  unit: ProtoUnit;
  open: boolean;
  problemCount: number;
  warnCount: number;
  onToggle: () => void;
}) {
  const derived = useMemo(() => deriveUnit(unit.text), [unit.text]);
  return (
    <article
      id={`proto-report-${unit.unitId}`}
      className={`rounded-[10px] border ${problemCount > 0 ? "border-red-500/40" : "border-hairline"}`}
      style={CARD_STYLE}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left"
      >
        <ChevronDown className={`h-3.5 w-3.5 text-text-4 transition-transform ${open ? "rotate-180" : ""}`} aria-hidden="true" />
        <span className="rounded bg-bg-grad-a/70 px-1.5 py-0.5 font-mono text-[11px] text-text-2">{unit.unitId}</span>
        <DurationSelect value={unit.duration} choices={DURATION_CHOICES} disabled />
        <span className="font-mono text-[10.5px] text-text-4">
          {derived.shotCount} 镜头 · {derived.utterances.length} 台词 · {derived.references.length} 参考
        </span>
        <span className="flex-1" />
        {problemCount > 0 && (
          <span className="rounded bg-red-500/15 px-1.5 py-0.5 text-[10px] text-red-300">{problemCount} 违约</span>
        )}
        {warnCount > 0 && (
          <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-300">{warnCount} 提醒</span>
        )}
        {problemCount === 0 && warnCount === 0 && (
          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400/80" aria-hidden="true" />
        )}
      </button>

      {open && (
        <div className="border-t border-hairline-soft px-3.5 py-3">
          <div className="mb-2 flex items-center gap-2">
            <span className="font-mono text-[10px] tracking-[0.08em] text-text-4">分镜文稿（只读）</span>
            <span className="flex-1" />
            <DerivedRefPills references={derived.references} />
          </div>
          <div className="flex flex-col gap-1">
            {derived.lines.map((line, i) => (
              <LineView key={i} line={line} />
            ))}
          </div>
          <p className="mb-1 mt-3 font-mono text-[10px] tracking-[0.08em] text-text-4">原文锚</p>
          <blockquote className="rounded-[8px] border-l-2 border-hairline-strong bg-bg-grad-a/30 p-2.5 text-[12px] leading-relaxed text-text-3">
            {unit.sourceText}
          </blockquote>
        </div>
      )}
    </article>
  );
}

export function VariantReport({ scenario }: { scenario: ProtoScenario }) {
  const [openId, setOpenId] = useState<string | null>(null);
  const quarantined = scenario.quarantined;

  const locate = (unitId: string) => {
    setOpenId(unitId);
    requestAnimationFrame(() => {
      document.getElementById(`proto-report-${unitId}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  };

  return (
    <div className="flex flex-col gap-3">
      <header
        className="sticky top-0 z-10 flex items-center justify-between gap-3 rounded-[10px] border border-hairline px-3.5 py-2.5 backdrop-blur-md"
        style={CARD_STYLE}
      >
        <div className="flex flex-col">
          <span className="text-[12.5px] font-medium text-text">第 3 集拆分审核</span>
          <span className="text-[11px] text-text-4">
            {scenario.units.length} 个生成单元 · 报告在上，逐单元细读在下
          </span>
        </div>
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
      </header>

      <ReportPanel scenario={scenario} onLocate={locate} />

      <div className="flex flex-col gap-2">
        {scenario.units.map((unit) => (
          <UnitRow
            key={unit.unitId}
            unit={unit}
            open={openId === unit.unitId}
            problemCount={scenario.violations.filter((v) => v.unitId === unit.unitId).length}
            warnCount={scenario.warnings.filter((w) => w.unitId === unit.unitId).length}
            onToggle={() => setOpenId((cur) => (cur === unit.unitId ? null : unit.unitId))}
          />
        ))}
      </div>
    </div>
  );
}
