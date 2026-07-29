/**
 * PROTOTYPE — throwaway（wayfinder #1481）。三变体共用的小渲染件。
 */
import type { ReactNode } from "react";
import { MENTION_RE, mentionNameFromMatch } from "@/utils/reference-mentions";
import { assetColor } from "@/components/canvas/reference/asset-colors";
import { PROTO_ASSETS, type ProtoLine } from "./mock";

/** mention 着色：登记按资产类型，未登记红 */
export function renderMentions(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  let key = 0;
  for (const m of text.matchAll(MENTION_RE)) {
    const start = m.index ?? 0;
    if (start > last) nodes.push(text.slice(last, start));
    const name = mentionNameFromMatch(m);
    const asset = PROTO_ASSETS[name];
    const palette = assetColor(asset?.kind ?? "unknown");
    nodes.push(
      <span key={key++} className={`rounded px-0.5 ${palette.textClass} ${palette.bgClass}`} translate="no">
        {m[0]}
      </span>,
    );
    last = start + m[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

/** 单行书写层文本的语义化渲染 */
export function LineView({ line }: { line: ProtoLine }) {
  if (line.kind === "shot") {
    const idx = line.raw.indexOf("：") >= 0 ? line.raw.indexOf("：") : line.raw.indexOf(":");
    const head = line.raw.slice(0, idx + 1);
    const rest = line.raw.slice(idx + 1);
    return (
      <p className="text-[12.5px] leading-relaxed text-text-2">
        <span className="mr-1 rounded bg-bg-grad-a/80 px-1 py-px font-mono text-[11px] font-semibold text-text">
          {head.replace(/[：:]$/, "")}
        </span>
        {renderMentions(rest)}
      </p>
    );
  }
  if (line.kind === "dialogue") {
    return (
      <p className="rounded-[6px] bg-accent/8 px-2 py-1 text-[12.5px] leading-relaxed text-text">
        <span className="mr-1.5 rounded bg-sky-500/15 px-1 py-px text-[11px] text-sky-300" translate="no">
          @[{line.speaker}]
        </span>
        <span className="text-text">{`{${line.quote ?? ""}}`}</span>
      </p>
    );
  }
  if (line.kind === "voiceover") {
    return (
      <p className="rounded-[6px] bg-bg-grad-a/40 px-2 py-1 text-[12.5px] italic leading-relaxed text-text-3">
        <span className="mr-1.5 rounded border border-hairline px-1 py-px text-[10px] not-italic text-text-4">画外音</span>
        {`{${line.quote ?? ""}}`}
      </p>
    );
  }
  if (!line.raw.trim()) return <div className="h-1.5" />;
  return <p className="text-[12.5px] leading-relaxed text-text-3">{renderMentions(line.raw)}</p>;
}

/** 派生参考图 pills：顺序即图片编号（图片N 序号 chip） */
export function DerivedRefPills({ references }: { references: { name: string; kind: "character" | "scene" | "prop" }[] }) {
  if (!references.length) return <span className="text-[10.5px] text-text-4">无参考图</span>;
  return (
    <div className="flex flex-wrap items-center gap-1">
      {references.map((ref, i) => {
        const palette = assetColor(ref.kind);
        return (
          <span
            key={ref.name}
            className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[10px] ${palette.textClass} ${palette.bgClass}`}
            translate="no"
          >
            <span aria-hidden="true" className="text-[9px] text-text-4">图片{i + 1}</span>
            <span aria-hidden="true" className={`h-[3px] w-[3px] rounded-full ${palette.dotClass}`} />
            {ref.name}
          </span>
        );
      })}
    </div>
  );
}

/** 时长枚举下拉（unit 单时长结构控件；档位为占位示意） */
export function DurationSelect({
  value,
  choices,
  disabled,
  onChange,
}: {
  value: number;
  choices: number[];
  disabled?: boolean;
  onChange?: (v: number) => void;
}) {
  return (
    <select
      value={value}
      disabled={disabled}
      onChange={(e) => onChange?.(Number(e.target.value))}
      aria-label="unit 时长"
      className="rounded-[6px] border border-hairline bg-bg-grad-a/55 px-1.5 py-0.5 font-mono text-[11px] text-text-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50"
    >
      {choices.map((c) => (
        <option key={c} value={c}>
          {c}s
        </option>
      ))}
    </select>
  );
}
