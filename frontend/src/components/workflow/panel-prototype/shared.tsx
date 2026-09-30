// PROTOTYPE（#2828，一次性代码，勿合入 main）
import { createContext, useContext, useState, type ReactNode } from "react";
import { useLocation, useSearch } from "wouter";
import { CONTENT_LABEL, GEN_LABEL, NOTE_COLOR, SCENARIOS, type Act, type Note, type Scenario } from "./model";

// ---- 点击回显：原型不接任何真实操作，点了什么、会发生什么都显示在左下角 ----------------

const LogCtx = createContext<(a: Act) => void>(() => {});
export const useLog = () => useContext(LogCtx);

export function LogProvider({ children }: { children: ReactNode }) {
  const [last, setLast] = useState<Act | null>(null);
  return (
    <LogCtx.Provider value={setLast}>
      {children}
      <ScenarioPicker last={last} />
    </LogCtx.Provider>
  );
}

export function useScenario(): Scenario {
  const search = useSearch();
  const id = new URLSearchParams(search).get("scenario");
  return SCENARIOS.find((s) => s.id === id) ?? SCENARIOS[0];
}

function ScenarioPicker({ last }: { last: Act | null }) {
  const [location, setLocation] = useLocation();
  const search = useSearch();
  const cur = useScenario();
  const choose = (id: string) => {
    const params = new URLSearchParams(search);
    params.set("scenario", id);
    setLocation(`${location}?${params.toString()}`, { replace: true });
  };
  const groups = (["narration", "drama", "ad"] as const).map((c) => ({
    c,
    items: SCENARIOS.filter((s) => s.content === c),
  }));
  if (import.meta.env.PROD) return null;
  return (
    <div
      className="fixed bottom-4 left-4 z-[100] w-[300px] space-y-1.5 rounded-xl p-2.5 text-[11.5px]"
      style={{ background: "oklch(0.95 0.12 95)", color: "oklch(0.2 0 0)", boxShadow: "0 8px 28px -6px oklch(0 0 0 / 0.6)" }}
    >
      <div className="font-semibold">PROTOTYPE · 现状场景</div>
      <select
        value={cur.id}
        onChange={(e) => choose(e.target.value)}
        className="w-full rounded border border-black/20 bg-white/70 px-1.5 py-1"
      >
        {groups.map((g) => (
          <optgroup key={g.c} label={CONTENT_LABEL[g.c]}>
            {g.items.map((s) => (
              <option key={s.id} value={s.id}>
                {GEN_LABEL[s.gen]} · {s.label}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      <div className="opacity-70">
        {CONTENT_LABEL[cur.content]} × {GEN_LABEL[cur.gen]}（与下方画布无关，画布是真实项目）
      </div>
      {last && (
        <div className="rounded bg-black/10 px-1.5 py-1">
          点了「{last.label}」{last.disabled ? `（不可点：${last.disabled}）` : ""}
          {last.effect && <div className="opacity-80">→ {last.effect}</div>}
        </div>
      )}
    </div>
  );
}

// ---- 基础控件 --------------------------------------------------------------------------

/** 主入口「交给 Agent」实心、次入口 AI 描边、跳转为文字链、破坏性为红字链。 */
export function ActButton({ act, size = "md" }: { act: Act; size?: "sm" | "md" | "lg" }) {
  const log = useLog();
  const pad = size === "lg" ? "px-3.5 py-1.5 text-[13px]" : size === "sm" ? "px-2 py-0.5 text-[11.5px]" : "px-2.5 py-1 text-[12px]";
  const disabled = Boolean(act.disabled);
  const common = {
    type: "button" as const,
    title: act.disabled,
    "aria-disabled": disabled,
    onClick: () => log(act),
  };
  if (act.kind === "agent") {
    return (
      <button
        {...common}
        className={`focus-ring rounded-md font-medium ${pad} ${disabled ? "cursor-not-allowed opacity-45" : "hover:opacity-90"}`}
        style={{ background: "linear-gradient(135deg, var(--color-accent-2), var(--color-accent))", color: "oklch(0.15 0 0)" }}
      >
        ✦ {act.label}
      </button>
    );
  }
  if (act.kind === "ai") {
    return (
      <button
        {...common}
        className={`focus-ring rounded-md font-medium ${pad} ${disabled ? "cursor-not-allowed opacity-45" : "hover:opacity-80"}`}
        style={{ border: "1px solid var(--color-accent-soft)", color: "var(--color-accent-2)" }}
      >
        {act.label}
      </button>
    );
  }
  return (
    <button
      {...common}
      className={`focus-ring rounded underline underline-offset-2 hover:opacity-80 ${size === "lg" ? "text-[12.5px]" : "text-[11.5px]"} ${disabled ? "cursor-not-allowed opacity-45" : ""}`}
      style={{ color: act.kind === "danger" ? "var(--color-danger-2)" : "var(--color-text-2)" }}
    >
      {act.label}
    </button>
  );
}

export function NoteLine({ note }: { note: Note }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 text-[11.5px]" style={{ color: NOTE_COLOR[note.tone] }}>
      <span>{note.tone === "info" ? "ⓘ" : "▲"} {note.text}</span>
      {note.act && <ActButton act={note.act} size="sm" />}
    </div>
  );
}

/** 「或者」后接的分岔选项。 */
export function Alternatives({ acts, size }: { acts?: Act[]; size?: "sm" | "md" | "lg" }) {
  if (!acts || acts.length === 0) return null;
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
      <span className="text-[11.5px]" style={{ color: "var(--color-text-4)" }}>或者</span>
      {acts.map((a, i) => (
        <span key={a.label} className="inline-flex items-center gap-x-2">
          {i > 0 && <span style={{ color: "var(--color-text-4)" }}>·</span>}
          <ActButton act={a.kind === "ai" ? { ...a, kind: "nav" } : a} size={size === "lg" ? "md" : "sm"} />
        </span>
      ))}
    </span>
  );
}

/** 附加指令：按集保存，重新生成时预填。undefined 表示该动作不带附加指令。 */
export function InstructionField({ value, compact }: { value?: string; compact?: boolean }) {
  const [v, setV] = useState(value ?? "");
  if (value === undefined) return null;
  return (
    <label className="block">
      {!compact && (
        <span className="mb-0.5 block text-[11px]" style={{ color: "var(--color-text-3)" }}>
          附加要求（可选，按本集保存）
        </span>
      )}
      <input
        value={v}
        onChange={(e) => setV(e.target.value)}
        placeholder="例如：旁白保持第一人称、镜头少用特写"
        className="focus-ring w-full rounded-md px-2 py-1 text-[12px]"
        style={{ background: "var(--color-surface-2)", border: "1px solid var(--color-hairline)", color: "var(--color-text)" }}
      />
    </label>
  );
}

export function AdDuration({ s }: { s: Scenario }) {
  if (!s.ad) return null;
  const over = s.ad.total != null && s.ad.total > s.ad.target * 1.1;
  return (
    <span
      className="rounded-full px-2 py-0.5 text-[11px] tabular-nums"
      title={over ? "超出目标时长 10% 以上，不阻断生成" : undefined}
      style={{
        border: `1px solid ${over ? "var(--color-warm-ring)" : "var(--color-hairline)"}`,
        color: over ? "var(--color-warm)" : "var(--color-text-3)",
      }}
    >
      总时长 {s.ad.total != null ? `${s.ad.total} 秒` : "—"} / 目标 {s.ad.target} 秒
    </span>
  );
}
