// PROTOTYPE（#2828，一次性代码，勿合入 main）
// 三个结构不同的 WorkflowPanel 形态：
// A 步骤清单：沿用现有面板骨架，收起时一行现状 + 下一步按钮，展开为步骤列表，下一步落在所属行内。
// B 横向步骤条：步骤压成一排短状态常驻可见，右侧固定一张下一步卡，点步骤展开该行详情。
// C 下一步为主：面板主体是一句「下一步」与附加要求，现状退为下方的事实表与「需要留意」清单。
import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { TONE_COLOR, type Row, type Scenario } from "./model";
import { ActButton, AdDuration, Alternatives, InstructionField, NoteLine } from "./shared";

const hair = { borderColor: "var(--color-hairline)" };

function Dot({ row }: { row: Row }) {
  const c = TONE_COLOR[row.tone];
  const base = "inline-block h-2 w-2 shrink-0 rounded-full";
  if (row.tone === "todo") return <span aria-hidden className={base} style={{ border: `1px dashed ${c}` }} />;
  if (row.tone === "partial") return <span aria-hidden className={base} style={{ background: `linear-gradient(90deg, ${c} 50%, transparent 50%)`, border: `1px solid ${c}` }} />;
  if (row.tone === "running") return <span aria-hidden className={`${base} motion-safe:animate-pulse`} style={{ background: c }} />;
  return <span aria-hidden className={base} style={{ background: c }} />;
}

function RowActs({ row }: { row: Row }) {
  if (!row.acts?.length && !row.notes?.length) return null;
  return (
    <div className="space-y-1">
      {row.notes?.map((n) => <NoteLine key={n.text} note={n} />)}
      {row.acts?.length ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {row.acts.map((a) => <ActButton key={a.label} act={a.kind === "ai" ? { ...a, kind: "nav" } : a} size="sm" />)}
        </div>
      ) : null}
    </div>
  );
}

// ---- A 步骤清单 ------------------------------------------------------------------------

export function VariantA({ s }: { s: Scenario }) {
  const [open, setOpen] = useState(true);
  const next = s.next;
  return (
    <section className="border-b px-4 py-2" style={hair}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="focus-ring flex items-center gap-1.5 rounded text-[12.5px] font-medium"
          style={{ color: "var(--color-text)" }}
        >
          <ChevronDown className="h-3.5 w-3.5" style={{ transform: open ? "none" : "rotate(-90deg)" }} />
          制作进度
        </button>
        <span className="min-w-0 flex-1 truncate text-[12px]" style={{ color: "var(--color-text-3)" }}>
          {s.summary}
        </span>
        <AdDuration s={s} />
        {!open && next && (
          <span className="flex items-center gap-2">
            <span className="text-[11.5px]" style={{ color: "var(--color-text-3)" }}>下一步</span>
            {next.primary.map((a) => <ActButton key={a.label} act={a} size="sm" />)}
          </span>
        )}
      </div>

      {open && (
        <ol className="m-0 mt-2 list-none space-y-0 p-0">
          {s.rows.map((row) => {
            const isNext = next?.rowKey === row.key;
            return (
              <li
                key={row.key}
                className="flex gap-2.5 rounded-md py-1 pl-1.5 pr-2"
                style={isNext ? { background: "var(--color-accent-dim)", boxShadow: "inset 2px 0 0 var(--color-accent-2)" } : undefined}
              >
                <span className="mt-[6px] flex"><Dot row={row} /></span>
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <span className="w-20 shrink-0 text-[12.5px] font-medium" style={{ color: "var(--color-text)" }}>{row.title}</span>
                    <span className="text-[12px]" style={{ color: row.tone === "warn" ? "var(--color-warm)" : "var(--color-text-2)" }}>{row.status}</span>
                  </div>
                  {(row.acts?.length || row.notes?.length) ? <div className="pl-[88px]"><RowActs row={row} /></div> : null}
                  {isNext && next && (
                    <div className="space-y-1.5 pl-[88px] pt-1">
                      <div className="text-[12px]" style={{ color: "var(--color-text)" }}>
                        <span className="font-medium" style={{ color: "var(--color-accent-2)" }}>下一步：{next.title}</span>
                        <span style={{ color: "var(--color-text-3)" }}> — {next.detail}</span>
                      </div>
                      {next.hint && <NoteLine note={next.hint} />}
                      <div className="max-w-[420px]"><InstructionField value={next.instruction} compact /></div>
                      <div className="flex flex-wrap items-center gap-2">
                        {next.primary.map((a) => <ActButton key={a.label} act={a} />)}
                        <Alternatives acts={next.alternatives} />
                      </div>
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

// ---- B 横向步骤条 ----------------------------------------------------------------------

export function VariantB({ s }: { s: Scenario }) {
  const next = s.next;
  const [picked, setPicked] = useState<string | null>(null);
  const selKey = picked ?? next?.rowKey ?? s.rows[0].key;
  const sel = s.rows.find((r) => r.key === selKey) ?? s.rows[0];
  return (
    <section className="border-b" style={hair}>
      <div className="flex items-stretch">
        <div className="min-w-0 flex-1 px-4 py-2.5">
          <div className="mb-2 flex items-center gap-2">
            <span className="text-[12.5px] font-medium" style={{ color: "var(--color-text)" }}>{s.episode}</span>
            <span className="text-[11.5px]" style={{ color: "var(--color-text-3)" }}>{s.summary}</span>
            <span className="flex-1" />
            <AdDuration s={s} />
          </div>
          <ol className="m-0 flex list-none gap-1 overflow-x-auto p-0">
            {s.rows.map((row) => {
              const isNext = next?.rowKey === row.key;
              const isSel = row.key === selKey;
              return (
                <li key={row.key} className="min-w-[72px] flex-1">
                  <button
                    type="button"
                    onClick={() => setPicked(row.key)}
                    className="focus-ring w-full rounded-md px-2 py-1.5 text-left"
                    style={{
                      background: isSel ? "var(--color-surface-2)" : "transparent",
                      border: `1px solid ${isNext ? "var(--color-accent-soft)" : "var(--color-hairline-soft)"}`,
                      borderTop: `2px solid ${TONE_COLOR[row.tone]}`,
                    }}
                  >
                    <div className="flex items-center gap-1.5 text-[11.5px] font-medium" style={{ color: "var(--color-text)" }}>
                      {row.title}
                      {row.notes?.some((n) => n.tone !== "info") && <span style={{ color: "var(--color-warm)" }}>▲</span>}
                    </div>
                    <div className="truncate text-[11px] tabular-nums" style={{ color: row.tone === "warn" ? "var(--color-warm)" : "var(--color-text-3)" }}>
                      {isNext ? "▸ 下一步" : row.short}
                    </div>
                  </button>
                </li>
              );
            })}
          </ol>
          <div className="mt-2 space-y-1 rounded-md px-2.5 py-1.5" style={{ background: "var(--color-surface-2)" }}>
            <div className="text-[12px]">
              <span className="font-medium" style={{ color: "var(--color-text)" }}>{sel.title}</span>
              <span style={{ color: sel.tone === "warn" ? "var(--color-warm)" : "var(--color-text-2)" }}>{" · "}{sel.status}</span>
            </div>
            <RowActs row={sel} />
          </div>
        </div>

        {next && (
          <aside className="w-[340px] shrink-0 space-y-2 border-l px-4 py-2.5" style={hair}>
            <div className="text-[10.5px] font-medium uppercase tracking-wider" style={{ color: "var(--color-text-4)" }}>下一步</div>
            <div className="text-[14px] font-semibold" style={{ color: "var(--color-text)" }}>{next.title}</div>
            <p className="text-[11.5px] leading-relaxed" style={{ color: "var(--color-text-3)" }}>{next.detail}</p>
            {next.hint && <NoteLine note={next.hint} />}
            <InstructionField value={next.instruction} />
            <div className="flex flex-wrap gap-2">
              {next.primary.map((a) => <ActButton key={a.label} act={a} />)}
            </div>
            <Alternatives acts={next.alternatives} />
          </aside>
        )}
      </div>
    </section>
  );
}

// ---- C 下一步为主 ----------------------------------------------------------------------

export function VariantC({ s }: { s: Scenario }) {
  const next = s.next;
  const [open, setOpen] = useState(true);
  const notes = s.rows.flatMap((r) => (r.notes ?? []).filter((n) => n.tone !== "info").map((n) => ({ row: r, n })));
  return (
    <section className="border-b px-4 py-3" style={hair}>
      <div className="flex items-start gap-4">
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="text-[11.5px]" style={{ color: "var(--color-text-4)" }}>{s.episode} · 下一步</span>
            <span className="text-[16px] font-semibold" style={{ color: "var(--color-text)" }}>{next?.title ?? "暂无"}</span>
            <AdDuration s={s} />
          </div>
          {next && (
            <>
              <p className="max-w-[640px] text-[12.5px] leading-relaxed" style={{ color: "var(--color-text-2)" }}>{next.detail}</p>
              {next.hint && <NoteLine note={next.hint} />}
              <div className="flex flex-wrap items-center gap-2">
                {next.instruction !== undefined && (
                  <div className="w-[320px]"><InstructionField value={next.instruction} compact /></div>
                )}
                {next.primary.map((a) => <ActButton key={a.label} act={a} size="lg" />)}
              </div>
              <Alternatives acts={next.alternatives} size="lg" />
            </>
          )}
        </div>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="focus-ring shrink-0 rounded px-1.5 py-0.5 text-[11.5px]"
          style={{ color: "var(--color-text-3)" }}
        >
          {open ? "收起现状" : `本集现状 · ${s.summary}`}
        </button>
      </div>

      {open && (
        <div className="mt-3 grid grid-cols-[1fr_280px] gap-4">
          <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[12px]">
            {s.rows.map((row) => (
              <div key={row.key} className="contents">
                <dt className="flex items-center gap-1.5" style={{ color: "var(--color-text-3)" }}>
                  <Dot row={row} /> {row.title}
                </dt>
                <dd className="m-0 flex flex-wrap items-baseline gap-x-2" style={{ color: row.tone === "warn" ? "var(--color-warm)" : "var(--color-text-2)" }}>
                  {row.status}
                  {row.acts?.map((a) => <ActButton key={a.label} act={a.kind === "ai" ? { ...a, kind: "nav" } : a} size="sm" />)}
                </dd>
              </div>
            ))}
          </dl>
          <div className="space-y-1.5">
            <div className="text-[10.5px] font-medium uppercase tracking-wider" style={{ color: "var(--color-text-4)" }}>需要留意</div>
            {notes.length === 0 ? (
              <div className="text-[11.5px]" style={{ color: "var(--color-text-4)" }}>没有</div>
            ) : (
              notes.map(({ row, n }) => <NoteLine key={row.key + n.text} note={{ ...n, text: `${row.title}：${n.text}` }} />)
            )}
            {s.rows.flatMap((r) => (r.notes ?? []).filter((n) => n.tone === "info")).map((n) => <NoteLine key={n.text} note={n} />)}
          </div>
        </div>
      )}
    </section>
  );
}
