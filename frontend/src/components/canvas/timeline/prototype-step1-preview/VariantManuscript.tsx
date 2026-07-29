/**
 * PROTOTYPE — throwaway（wayfinder #1481）。
 *
 * 变体 A「文稿流」（用户拍板胜出，迭代中）：每 unit 一卡 —— 头部（U 号/时长/统计）
 * → 原文（默认展开、弱化引文）→ 分镜文稿（镜头行 + 缩进挂靠的台词/警示）→ 卡底参考图小节。
 * warning / 违约行内锚定；顶部单条状态条聚合违约定位入口。
 * gate 与编辑器解析预览复用同一文稿渲染形态。
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
  const anchorBroken = violations.some((v) => v.kind === "原文锚失配");
  const unanchored = violations.filter((v) => v.line === undefined);

  return (
    <article
      id={`proto-unit-${unit.unitId}`}
      className={`rounded-[10px] border p-4 ${hasViolation ? "border-red-500/45" : "border-hairline"}`}
      style={CARD_STYLE}
    >
      {/* 头部：只留身份 + 契约参数 + 统计 */}
      <div className="flex items-center gap-2">
        <span className="rounded bg-bg-grad-a/70 px-1.5 py-0.5 font-mono text-[11px] text-text-2">{unit.unitId}</span>
        <DurationSelect value={duration} choices={DURATION_CHOICES} onChange={setDuration} />
        <span className="text-[10.5px] text-text-4">
          {derived.shotCount} 镜头 · {derived.utterances.length} 台词
        </span>
        <span className="flex-1" />
        <button
          type="button"
          onClick={() => setEditing((e) => !e)}
          aria-label={editing ? "完成编辑" : "编辑文稿"}
          className={`rounded-[6px] p-1 transition-colors ${editing ? "bg-accent/20 text-accent" : "text-text-4 hover:text-text"}`}
        >
          <Pencil className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* 原文：弱化引文，默认展开可折叠 */}
      <details open className="group mt-3">
        <summary className="flex cursor-pointer list-none items-center gap-1 font-mono text-[10px] tracking-[0.08em] text-text-4">
          <ChevronDown className="h-3 w-3 transition-transform group-open:rotate-180" aria-hidden="true" />
          原文
          {anchorBroken && <span className="ml-1 rounded bg-red-500/15 px-1 py-px text-[10px] text-red-300">非源文逐字子串</span>}
        </summary>
        <p
          className={`mt-1.5 border-l pl-3 text-[11.5px] leading-relaxed ${
            anchorBroken ? "border-red-400/50 text-red-200/70" : "border-hairline text-text-4"
          }`}
        >
          {unit.sourceText}
        </p>
      </details>

      {/* 分镜文稿：镜头行顶格，台词/画外音/警示缩进挂靠 */}
      <div className="mt-3">
        {editing ? (
          <AutoTextarea value={text} onChange={setText} aria-label={`${unit.unitId} 分镜文稿`} className="font-mono text-[12px]" />
        ) : (
          <div className="flex flex-col gap-1">
            {derived.lines.map((line, i) => {
              const lineViolations = violations.filter((v) => v.line === i);
              const lineWarnings = warnings.filter((w) => w.line === i);
              const indent = line.kind !== "shot";
              return (
                <div key={i} className={indent ? "pl-5" : "pt-0.5"}>
                  <LineView line={line} />
                  {lineViolations.map((v) => (
                    <p key={v.kind} className="mt-1 flex items-start gap-1.5 pl-1 text-[11px] leading-snug text-red-300">
                      <OctagonAlert className="mt-px h-3 w-3 shrink-0" aria-hidden="true" />
                      <span>
                        {v.detail}
                        <span className="block text-red-300/60">修复：{v.fix}</span>
                      </span>
                    </p>
                  ))}
                  {lineWarnings.map((w) => (
                    <p key={w.text} className="mt-1 flex items-start gap-1.5 pl-1 text-[11px] leading-snug text-amber-300/80">
                      <AlertTriangle className="mt-px h-3 w-3 shrink-0" aria-hidden="true" />
                      {w.text}
                    </p>
                  ))}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {unanchored.filter((v) => v.kind !== "原文锚失配").map((v) => (
        <p key={v.kind} className="mt-2 flex items-start gap-1.5 text-[11px] leading-snug text-red-300">
          <OctagonAlert className="mt-px h-3 w-3 shrink-0" aria-hidden="true" />
          <span>
            {v.detail}
            <span className="block text-red-300/60">修复：{v.fix}</span>
          </span>
        </p>
      ))}

      {/* 卡底：派生参考图小节 */}
      <div className="mt-3 flex items-center gap-2 border-t border-hairline-soft pt-2.5">
        <span className="font-mono text-[10px] tracking-[0.08em] text-text-4">参考图</span>
        <DerivedRefPills references={derived.references} />
      </div>

      {quarantined && hasViolation && (
        <p className="mt-2 text-[10.5px] text-text-4">本 unit 的修复由助手在草稿上完成，晋升后此处更新。</p>
      )}
    </article>
  );
}

export function VariantManuscript({ scenario }: { scenario: ProtoScenario }) {
  const totalWarnings = scenario.warnings.length;
  const quarantined = scenario.quarantined;
  const violatingUnits = [...new Set(scenario.violations.map((v) => v.unitId))];

  return (
    <div className="flex flex-col gap-3">
      <header
        className="sticky top-0 z-10 flex items-center justify-between gap-3 rounded-[10px] border border-hairline px-3.5 py-2.5 backdrop-blur-md"
        style={CARD_STYLE}
      >
        <div className="flex items-center gap-2">
          {quarantined ? (
            <OctagonAlert className="h-4 w-4 shrink-0 text-red-400" />
          ) : (
            <CheckCircle2 className="h-4 w-4 shrink-0 text-amber-400" />
          )}
          <div className="flex flex-col">
            <span className="text-[12.5px] font-medium text-text">
              {quarantined ? "草稿隔离中 — 拆分未通过校验" : "第 3 集拆分待确认"}
            </span>
            <span className="text-[11px] text-text-4">
              {quarantined ? (
                <>
                  {violatingUnits.map((uid) => (
                    <button
                      key={uid}
                      type="button"
                      onClick={() =>
                        document.getElementById(`proto-unit-${uid}`)?.scrollIntoView({ behavior: "smooth", block: "center" })
                      }
                      className="text-red-300 underline decoration-red-300/40 underline-offset-2 hover:decoration-red-300"
                    >
                      {uid} · {scenario.violations.filter((v) => v.unitId === uid).length} 条违约
                    </button>
                  ))}
                  <span> — 点击定位 · 违约产物停在隔离草稿 · {totalWarnings} 条提醒</span>
                </>
              ) : totalWarnings > 0 ? (
                `${totalWarnings} 条提醒 · 不阻断确认`
              ) : (
                "无提醒"
              )}
            </span>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
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
