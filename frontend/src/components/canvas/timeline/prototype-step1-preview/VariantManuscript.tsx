/**
 * PROTOTYPE — throwaway（wayfinder #1481）。
 *
 * 变体 A「文稿流」：书写层文本是主角。每 unit 一卡，正文即高亮渲染的分镜文稿，
 * warning / 违约行内锚定（gutter 圆点 + 行下说明），原文锚折叠对照在卡底。
 * 对第三问的回答：gate 与编辑器解析预览**复用**同一文稿渲染形态——gate 即「编辑器 + 确认条」。
 */
import { useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronDown, Lock, OctagonAlert, Pencil } from "lucide-react";
import { AutoTextarea } from "@/components/ui/AutoTextarea";
import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE, CARD_STYLE, GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";
import {
  DURATION_CHOICES,
  deriveUnit,
  type ProtoScenario,
  type ProtoUnit,
  type ProtoViolation,
  type ProtoWarning,
} from "./mock";
import { DerivedRefPills, DurationSelect, LineView } from "./shared";

function UnitCard({
  unit,
  violations,
  warnings,
  quarantined,
}: {
  unit: ProtoUnit;
  violations: ProtoViolation[];
  warnings: ProtoWarning[];
  quarantined: boolean;
}) {
  const [text, setText] = useState(unit.text);
  const [duration, setDuration] = useState(unit.duration);
  const [editing, setEditing] = useState(false);
  const derived = useMemo(() => deriveUnit(text), [text]);
  const hasViolation = violations.length > 0;

  const byLine = (line: number) => ({
    violations: violations.filter((v) => v.line === line),
    warnings: warnings.filter((w) => w.line === line),
  });
  const unanchored = violations.filter((v) => v.line === undefined);

  return (
    <article
      id={`proto-unit-${unit.unitId}`}
      className={`rounded-[10px] border p-3.5 ${hasViolation ? "border-red-500/45" : "border-hairline"}`}
      style={CARD_STYLE}
    >
      <div className="mb-2.5 flex items-center gap-2">
        <span className="rounded bg-bg-grad-a/70 px-1.5 py-0.5 font-mono text-[11px] text-text-2">{unit.unitId}</span>
        <DurationSelect value={duration} choices={DURATION_CHOICES} onChange={setDuration} />
        <span className="text-[10.5px] text-text-4">{derived.shotCount} 镜头 · {derived.utterances.length} 台词</span>
        <span className="flex-1" />
        <DerivedRefPills references={derived.references} />
        <button
          type="button"
          onClick={() => setEditing((e) => !e)}
          aria-label={editing ? "完成编辑" : "编辑文稿"}
          className={`rounded-[6px] p-1 transition-colors ${editing ? "bg-accent/20 text-accent" : "text-text-4 hover:text-text"}`}
        >
          <Pencil className="h-3.5 w-3.5" />
        </button>
      </div>

      {editing ? (
        <AutoTextarea value={text} onChange={setText} aria-label={`${unit.unitId} 分镜文稿`} className="font-mono text-[12px]" />
      ) : (
        <div className="flex flex-col gap-1">
          {derived.lines.map((line, i) => {
            const anchored = byLine(i);
            const marked = anchored.violations.length > 0 || anchored.warnings.length > 0;
            return (
              <div key={i} className="grid grid-cols-[10px_1fr] gap-1.5">
                <span className="flex justify-center pt-2">
                  {anchored.violations.length > 0 ? (
                    <span className="h-1.5 w-1.5 rounded-full bg-red-400" aria-label="违约" />
                  ) : anchored.warnings.length > 0 ? (
                    <span className="h-1.5 w-1.5 rounded-full bg-amber-400" aria-label="提醒" />
                  ) : null}
                </span>
                <div className={marked ? "rounded-[6px] outline outline-1 outline-offset-2 outline-transparent" : undefined}>
                  <LineView line={line} />
                  {anchored.violations.map((v) => (
                    <p key={v.kind} className="mt-0.5 flex items-start gap-1 pl-1 text-[11px] text-red-300">
                      <OctagonAlert className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
                      <span>
                        <span className="mr-1 rounded bg-red-500/15 px-1 py-px text-[10px]">{v.kind}</span>
                        {v.detail}
                        <span className="block text-red-300/70">修复：{v.fix}</span>
                      </span>
                    </p>
                  ))}
                  {anchored.warnings.map((w) => (
                    <p key={w.text} className="mt-0.5 flex items-start gap-1 pl-1 text-[11px] text-amber-300/90">
                      <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
                      {w.text}
                    </p>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {unanchored.length > 0 && (
        <div className="mt-2 rounded-[8px] border border-red-500/30 bg-red-500/8 p-2">
          {unanchored.map((v) => (
            <p key={v.kind} className="text-[11px] text-red-300">
              <span className="mr-1 rounded bg-red-500/15 px-1 py-px text-[10px]">{v.kind}</span>
              {v.detail}
              <span className="block text-red-300/70">修复：{v.fix}</span>
            </p>
          ))}
        </div>
      )}

      <details className="group mt-2.5">
        <summary className="flex cursor-pointer list-none items-center gap-1 text-[10.5px] tracking-[0.08em] text-text-4">
          <ChevronDown className="h-3 w-3 transition-transform group-open:rotate-180" aria-hidden="true" />
          原文对照
        </summary>
        <blockquote
          className={`mt-1.5 rounded-[8px] border-l-2 p-2.5 text-[12px] leading-relaxed text-text-3 ${
            unanchored.some((v) => v.kind === "原文锚失配") ? "border-red-400/60 bg-red-500/5" : "border-hairline-strong bg-bg-grad-a/30"
          }`}
        >
          {unit.sourceText}
        </blockquote>
      </details>
      {quarantined && hasViolation && (
        <p className="mt-2 text-[10.5px] text-text-4">本 unit 的修复由助手在草稿上完成，晋升后此处更新。</p>
      )}
    </article>
  );
}

export function VariantManuscript({ scenario }: { scenario: ProtoScenario }) {
  const totalViolations = scenario.violations.length;
  const totalWarnings = scenario.warnings.length;
  const quarantined = scenario.quarantined;

  return (
    <div className="flex flex-col gap-3">
      <header
        className="sticky top-0 z-10 flex items-center justify-between gap-3 rounded-[10px] border border-hairline px-3.5 py-2.5 backdrop-blur-md"
        style={CARD_STYLE}
      >
        <div className="flex items-center gap-2">
          {quarantined ? (
            <OctagonAlert className="h-4 w-4 text-red-400" />
          ) : (
            <CheckCircle2 className="h-4 w-4 text-amber-400" />
          )}
          <div className="flex flex-col">
            <span className="text-[12.5px] font-medium text-text">
              {quarantined ? "草稿隔离中 — 拆分未通过校验" : "第 3 集拆分待确认"}
            </span>
            <span className="text-[11px] text-text-4">
              {quarantined
                ? `${totalViolations} 条违约待修复 · ${totalWarnings} 条提醒`
                : totalWarnings > 0
                  ? `${totalWarnings} 条提醒 · 不阻断确认`
                  : "无提醒"}
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

      {quarantined && (
        <button
          type="button"
          onClick={() => document.getElementById("proto-unit-U3")?.scrollIntoView({ behavior: "smooth", block: "center" })}
          className="flex items-center gap-2 rounded-[10px] border border-red-500/35 bg-red-500/8 px-3.5 py-2 text-left text-[11.5px] text-red-300 transition-colors hover:bg-red-500/12"
        >
          <OctagonAlert className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          U3 有 {totalViolations} 条违约 — 点击定位。违约产物停在隔离草稿，不会覆盖已确认内容。
        </button>
      )}

      <div className="flex flex-col gap-2.5">
        {scenario.units.map((unit) => (
          <UnitCard
            key={unit.unitId}
            unit={unit}
            violations={scenario.violations.filter((v) => v.unitId === unit.unitId)}
            warnings={scenario.warnings.filter((w) => w.unitId === unit.unitId)}
            quarantined={quarantined}
          />
        ))}
      </div>
    </div>
  );
}
