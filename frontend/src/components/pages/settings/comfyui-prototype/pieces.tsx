// PROTOTYPE — 一次性代码，不进 main。
// 三个变体共用的「块」：状态标签、候选列表、导入面板、连通性徽标、模型行编辑、端点测试。
// 只共用块，不共用布局——每个变体自己决定这些块摆在哪、以什么顺序出现。
import { CheckCircle2, ChevronDown, ChevronRight, Loader2, Play, RefreshCw, Sparkles, Upload, X } from "lucide-react";
import { useMemo, useRef, useState, type ReactNode } from "react";

import { ACCENT_BTN_SM_CLS, ACCENT_BUTTON_STYLE, GHOST_BTN_CLS, INPUT_CLS } from "@/components/ui/darkroom-tokens";

import {
  deriveValues,
  renderPromptBody,
  SAMPLES,
  SLOT_BY_ID,
  SLOTS,
  writableInputs,
  type Candidate,
  type ComfyProto,
  type ImportShape,
  type SlotBinding,
  type SlotId,
  type SlotStatus,
  type WorkflowEndpoint,
} from "./fixtures";

export const KICKER_CLS = "font-mono text-[9.5px] font-bold uppercase tracking-[0.16em] text-text-4";
export const KICKER_ACCENT_CLS = "font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-accent-2";
export const LABEL_CLS = "mb-1 block text-[11.5px] font-medium text-text-3";
export const MONO_CLS = "font-mono text-[11.5px]";

// ---------- 状态 ----------

const STATUS_STYLE: Record<SlotStatus, { label: string; cls: string }> = {
  auto: { label: "自动识别", cls: "border-good/40 bg-good/10 text-good" },
  manual: { label: "手动指定", cls: "border-accent/40 bg-accent-dim text-accent-2" },
  ambiguous: { label: "需选择", cls: "border-warn/50 bg-warn/10 text-warn" },
  missing: { label: "未找到", cls: "border-hairline-strong bg-bg-grad-a/60 text-text-3" },
  unsupported: { label: "不支持", cls: "border-hairline-soft bg-transparent text-text-4 line-through decoration-text-4/60" },
};

export function StatusPill({ status, required }: { status: SlotStatus; required?: boolean }) {
  const s = STATUS_STYLE[status];
  const blocking = (required && status !== "auto" && status !== "manual") || status === "ambiguous";
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px] font-semibold ${blocking ? "border-danger/50 bg-danger/10 text-danger" : s.cls}`}>
      {blocking && status !== "ambiguous" ? "必填未绑定" : s.label}
    </span>
  );
}

export function SlotTag({ id, size = "md" }: { id: SlotId; size?: "sm" | "md" }) {
  const meta = SLOT_BY_ID[id];
  return (
    <span className={`inline-flex items-baseline gap-1.5 ${size === "sm" ? "text-[11px]" : "text-[12.5px]"}`}>
      <span className={`${MONO_CLS} text-accent-2`}>{id}</span>
      <span className="text-text-2">{meta.label}</span>
      {meta.required && <span className="text-danger" title="必填">*</span>}
    </span>
  );
}

export function TargetLabel({ c, showTitle = true }: { c: Candidate; showTitle?: boolean }) {
  return (
    <span className={`${MONO_CLS} text-text`}>
      #{c.node}
      <span className="text-text-3"> {c.class_type}</span>
      {c.input && <span className="text-text"> .{c.input}</span>}
      {showTitle && c.title && <span className="ml-1.5 font-sans text-[11px] text-text-4">“{c.title}”</span>}
    </span>
  );
}

export function ScoreBar({ score, max }: { score: number; max: number }) {
  const pct = Math.max(6, Math.round((score / Math.max(max, 1)) * 100));
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="h-1 w-14 overflow-hidden rounded-full bg-bg-grad-a">
        <span className="block h-full rounded-full bg-accent" style={{ width: `${pct}%` }} />
      </span>
      <span className="font-mono text-[10px] text-text-4">{score}</span>
    </span>
  );
}

// ---------- 候选列表 / 手选 ----------

interface CandidateListProps {
  slot: SlotId;
  binding: SlotBinding;
  proto: ComfyProto;
  /** 列表里展示信号说明 */
  verbose?: boolean;
}

export function CandidateList({ slot, binding, proto, verbose = true }: CandidateListProps) {
  const max = binding.candidates[0]?.score ?? 1;
  return (
    <ul className="space-y-1">
      {binding.candidates.map((c) => {
        const chosen = binding.chosen?.node === c.node && binding.chosen?.input === c.input;
        return (
          <li key={`${c.node}.${c.input ?? ""}`}>
            <button
              type="button"
              onClick={() => proto.choose(slot, c)}
              className={`flex w-full items-start gap-2.5 rounded-[7px] border px-2.5 py-1.5 text-left transition-colors ${chosen ? "border-accent/45 bg-accent-dim" : "border-hairline-soft hover:border-hairline"}`}
            >
              <span className={`mt-[3px] h-3 w-3 shrink-0 rounded-full border ${chosen ? "border-accent-2 bg-accent-2" : "border-hairline-strong"}`} />
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-center justify-between gap-x-3">
                  <TargetLabel c={c} />
                  <ScoreBar score={c.score} max={max} />
                </span>
                {verbose && <span className="mt-0.5 block text-[11px] text-text-4">{c.signals.join(" · ")}</span>}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export function ManualPicker({ slot, ep, proto, compact }: { slot: SlotId; ep: WorkflowEndpoint; proto: ComfyProto; compact?: boolean }) {
  const isOutput = slot === "output";
  const options = useMemo(() => {
    const out: { key: string; label: string; c: Candidate }[] = [];
    for (const n of ep.nodes) {
      if (isOutput) {
        out.push({ key: n.id, label: `#${n.id} ${n.class_type}${n.title ? ` “${n.title}”` : ""}`, c: { node: n.id, class_type: n.class_type, title: n.title, score: 0, signals: ["手动指定"] } });
        continue;
      }
      for (const [k, v] of writableInputs(n)) {
        out.push({ key: `${n.id}.${k}`, label: `#${n.id} ${n.class_type}.${k} = ${JSON.stringify(v).slice(0, 28)}`, c: { node: n.id, input: k, class_type: n.class_type, title: n.title, score: 0, signals: ["手动指定"] } });
      }
    }
    return out;
  }, [ep.nodes, isOutput]);
  return (
    <select
      className={`${INPUT_CLS} ${compact ? "py-1 text-[12px]" : ""}`}
      value=""
      onChange={(e) => {
        const hit = options.find((o) => o.key === e.target.value);
        if (hit) proto.choose(slot, hit.c);
      }}
      aria-label={`手动为 ${slot} 选择目标`}
    >
      <option value="">{isOutput ? "手动选择产物节点…" : "从全部 节点 · 字段 里手选…"}</option>
      {options.map((o) => (
        <option key={o.key} value={o.key}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

export function SlotExtras({ slot, binding, proto }: { slot: SlotId; binding: SlotBinding; proto: ComfyProto }) {
  if (slot === "width" || slot === "height" || slot === "frames") {
    return (
      <label className="inline-flex items-center gap-1.5 text-[11.5px] text-text-3">
        对齐步长
        <input
          type="number"
          min={1}
          className={`${INPUT_CLS} w-16 py-0.5 text-[12px]`}
          value={binding.step ?? ""}
          onChange={(e) => proto.setStep(slot, Number(e.target.value) || 1)}
        />
        {slot === "frames" && <span className="text-text-4">帧数 = step·n + 1</span>}
      </label>
    );
  }
  if (slot === "seed") {
    return (
      <span className="inline-flex items-center gap-2 text-[11.5px] text-text-3">
        策略
        {(["random", "keep"] as const).map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => proto.setSeedPolicy(p)}
            className={`rounded-full border px-2 py-0.5 font-mono text-[10.5px] ${binding.policy === p ? "border-accent/45 bg-accent-dim text-accent-2" : "border-hairline-soft text-text-3"}`}
          >
            {p === "random" ? "random · 每次换新" : "keep · 用 workflow 里的值"}
          </button>
        ))}
      </span>
    );
  }
  if (slot === "fps") {
    return <span className="text-[11.5px] text-text-4">只读：用于帧数换算与产物帧率，ArcReel 不会写它</span>;
  }
  return null;
}

/** 一个槽位的操作按钮组：标为不支持 / 重新识别。 */
export function SlotActions({ slot, binding, proto }: { slot: SlotId; binding: SlotBinding; proto: ComfyProto }) {
  const meta = SLOT_BY_ID[slot];
  return (
    <span className="inline-flex items-center gap-1">
      {!meta.required && binding.status !== "unsupported" && (
        <button type="button" className={`${GHOST_BTN_CLS} px-2 py-1 text-[11px]`} onClick={() => proto.markUnsupported(slot)} title="此 workflow 不支持该槽位，保存为空列表">
          <X className="h-3 w-3" /> 标为不支持
        </button>
      )}
      <button type="button" className={`${GHOST_BTN_CLS} px-2 py-1 text-[11px]`} onClick={() => proto.reinfer(slot)} title="丢掉手动修改，重新跑推断">
        <RefreshCw className="h-3 w-3" /> 重新识别
      </button>
    </span>
  );
}

// ---------- 导入 ----------

interface ImportPanelProps {
  proto: ComfyProto;
  onImported: (shape: ImportShape) => void;
  /** 面板嵌在哪里：modal 里给大留白，inline 收紧 */
  dense?: boolean;
}

export function ImportPanel({ proto, onImported, dense }: ImportPanelProps) {
  const [text, setText] = useState("");
  const [fileName, setFileName] = useState<string | undefined>();
  const [shape, setShape] = useState<ImportShape | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const run = (t: string, f?: string) => {
    const s = proto.importText(t, f);
    setShape(s);
    if (s.kind === "raw" || s.kind === "definition") onImported(s);
  };
  const loadSample = (k: keyof typeof SAMPLES) => {
    setText(SAMPLES[k].text);
    setFileName(SAMPLES[k].file);
    setShape(null);
  };

  return (
    <div className={dense ? "space-y-2.5" : "space-y-3"}>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className={GHOST_BTN_CLS} onClick={() => fileRef.current?.click()}>
          <Upload className="h-3.5 w-3.5" /> 选择 JSON 文件
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".json,application/json"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (!f) return;
            e.target.value = "";
            void f.text().then((t) => {
              setText(t);
              setFileName(f.name);
              run(t, f.name);
            });
          }}
        />
        <span className="text-[11.5px] text-text-4">或粘贴内容</span>
        <span className="ml-auto inline-flex items-center gap-1 text-[11px] text-text-4">
          示例：
          {(Object.keys(SAMPLES) as (keyof typeof SAMPLES)[]).map((k) => (
            <button key={k} type="button" onClick={() => loadSample(k)} className="rounded-[5px] border border-hairline-soft px-1.5 py-0.5 font-mono text-[10.5px] text-text-3 hover:border-hairline hover:text-text">
              {k}
            </button>
          ))}
        </span>
      </div>
      <textarea
        className={`${INPUT_CLS} ${MONO_CLS} ${dense ? "h-32" : "h-44"} resize-y leading-[1.5]`}
        placeholder='粘贴 ComfyUI「Export (API)」导出的 JSON，或一份带 "kind": "comfyui" 的端点定义'
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setShape(null);
        }}
      />
      <div className="flex items-center gap-3">
        <button type="button" className={ACCENT_BTN_SM_CLS} style={ACCENT_BUTTON_STYLE} disabled={!text.trim()} onClick={() => run(text, fileName)}>
          <Sparkles className="h-3.5 w-3.5" /> 识别并推断绑定
        </button>
        {fileName && <span className={`${MONO_CLS} text-text-4`}>{fileName}</span>}
      </div>
      {shape && <ShapeNotice shape={shape} />}
    </div>
  );
}

export function ShapeNotice({ shape }: { shape: ImportShape }) {
  if (shape.kind === "raw") {
    const n = Object.values(shape.bindings);
    const auto = n.filter((b) => b.status === "auto").length;
    const amb = n.filter((b) => b.status === "ambiguous").length;
    const miss = n.filter((b) => b.status === "missing").length;
    return (
      <div className="rounded-[8px] border border-good/35 bg-good/10 px-3 py-2 text-[12px] text-text-2">
        识别为 <b>API 格式 workflow</b>（{shape.nodes.length} 个节点）。已自动包装为 workflow 端点并推断绑定：自动识别 {auto}、需选择 {amb}、未找到 {miss}。
      </div>
    );
  }
  if (shape.kind === "definition") {
    return (
      <div className="rounded-[8px] border border-good/35 bg-good/10 px-3 py-2 text-[12px] text-text-2">
        识别为 <b>workflow 端点定义</b>「{shape.name}」，绑定表按文件原样载入；你仍会进入绑定编辑器确认一遍。
      </div>
    );
  }
  const tone = shape.kind === "ui" ? "border-warn/50 bg-warn/10 text-warn" : "border-danger/50 bg-danger/10 text-danger";
  return <div className={`rounded-[8px] border px-3 py-2 text-[12px] ${tone}`}>{shape.message}</div>;
}

// ---------- 服务 ----------

export function ConnectivityBadge({ proto }: { proto: ComfyProto }) {
  const c = proto.provider.check;
  if (c.status === "idle") return <span className="text-[11.5px] text-text-4">尚未测试连接</span>;
  if (c.status === "checking")
    return (
      <span className="inline-flex items-center gap-1.5 text-[11.5px] text-text-3">
        <Loader2 className="h-3 w-3 animate-spin" /> 正在请求 /system_stats…
      </span>
    );
  if (c.status === "ok")
    return (
      <span className="inline-flex items-center gap-1.5 text-[11.5px] text-good">
        <CheckCircle2 className="h-3.5 w-3.5" /> 已连通 · ComfyUI <span className={MONO_CLS}>v{c.version}</span>
        <span className="text-text-4">· {c.message}</span>
      </span>
    );
  return <span className="text-[11.5px] text-danger">连接失败：{c.message}</span>;
}

export function ProviderFields({ proto, layout = "stack" }: { proto: ComfyProto; layout?: "stack" | "grid" }) {
  const p = proto.provider;
  const cls = layout === "grid" ? "grid grid-cols-1 gap-3 md:grid-cols-3" : "space-y-3";
  return (
    <div className={cls}>
      <label className="block">
        <span className={LABEL_CLS}>显示名</span>
        <input className={INPUT_CLS} value={p.name} onChange={(e) => proto.setProvider({ name: e.target.value })} />
      </label>
      <label className="block">
        <span className={LABEL_CLS}>服务地址</span>
        <input className={`${INPUT_CLS} ${MONO_CLS}`} value={p.baseUrl} placeholder="http://127.0.0.1:8188" onChange={(e) => proto.setProvider({ baseUrl: e.target.value })} />
      </label>
      <label className="block">
        <span className={LABEL_CLS}>
          API Key <span className="font-normal text-text-4">（可留空；ComfyUI 本体无鉴权）</span>
        </span>
        <input className={`${INPUT_CLS} ${MONO_CLS}`} type="password" value={p.apiKey} placeholder="仅当前置了反代鉴权时填写" onChange={(e) => proto.setProvider({ apiKey: e.target.value })} />
      </label>
    </div>
  );
}

export function ProtocolRow() {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-text-3">
      <span>
        模型发现协议 <span className={`${MONO_CLS} rounded-[5px] border border-hairline-soft px-1.5 py-0.5 text-text-2`}>comfyui</span>
      </span>
      <span className="text-text-4">「发现模型」不适用：模型行来自你导入的 workflow 端点</span>
    </div>
  );
}

// ---------- 模型行 ----------

export function ModelRowsEditor({ proto, endpointFilter }: { proto: ComfyProto; endpointFilter?: string }) {
  const rows = endpointFilter ? proto.models.filter((m) => m.endpointKey === endpointFilter) : proto.models;
  const eps = proto.endpoints;
  return (
    <div className="space-y-2">
      {rows.length === 0 && <p className="text-[12px] text-text-4">还没有模型行。一行模型 = 一个可在项目里选择的名字，挂接一份 workflow 端点。</p>}
      {rows.map((m) => (
        <div key={m.id} className="grid grid-cols-[1fr_1.2fr_auto_auto_auto] items-center gap-2 rounded-[8px] border border-hairline-soft bg-bg-grad-a/30 px-3 py-2">
          <input className={`${INPUT_CLS} py-1 text-[12.5px]`} value={m.name} onChange={(e) => proto.updateModel(m.id, { name: e.target.value })} aria-label="模型显示名" />
          <select className={`${INPUT_CLS} ${MONO_CLS} py-1`} value={m.endpointKey} onChange={(e) => proto.updateModel(m.id, { endpointKey: e.target.value })} aria-label="调用端点">
            {eps.map((e) => (
              <option key={e.key} value={e.key}>
                {e.key} · {e.mediaType} · {e.name}
              </option>
            ))}
          </select>
          <label className="inline-flex items-center gap-1 text-[11.5px] text-text-3">
            <input type="radio" name={`default-${endpointFilter ?? "all"}`} checked={m.isDefault} onChange={() => proto.updateModel(m.id, { isDefault: true })} /> 默认
          </label>
          <label className="inline-flex items-center gap-1 text-[11.5px] text-text-3">
            单价
            <input className={`${INPUT_CLS} w-16 py-1 text-[12px]`} value={m.price} onChange={(e) => proto.updateModel(m.id, { price: e.target.value })} />
          </label>
          <button type="button" className={`${GHOST_BTN_CLS} px-2 text-danger`} onClick={() => proto.removeModel(m.id)} aria-label="移除模型行">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
      <div className="flex items-center gap-2">
        <button type="button" className={GHOST_BTN_CLS} disabled={eps.length === 0} onClick={() => proto.addModel(endpointFilter ?? eps[0].key)}>
          + 新增模型行
        </button>
        {eps.length === 0 && <span className="text-[11.5px] text-text-4">先保存一份 workflow 端点，才有可挂接的目标</span>}
        <span className="ml-auto text-[11px] text-text-4">端点选择器只列本供应商协议下的 workflow 端点；费用固定 0，单价仅供企业分摊手填</span>
      </div>
    </div>
  );
}

// ---------- 端点测试 ----------

export function PreviewRequestBlock({ ep, proto }: { ep: WorkflowEndpoint; proto: ComfyProto }) {
  const [open, setOpen] = useState(false);
  const values = useMemo(() => deriveValues(ep, proto.ctx), [ep, proto.ctx, proto.previewSeq]); // eslint-disable-line react-hooks/exhaustive-deps
  const body = useMemo(() => renderPromptBody(ep, proto.ctx, values), [ep, proto.ctx, values]);
  const headers = ["Content-Type: application/json", ...(proto.provider.apiKey ? ["Authorization: Bearer ••••••••"] : [])];
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className={GHOST_BTN_CLS}
          onClick={() => {
            proto.bumpPreview();
            setOpen(true);
          }}
        >
          <RefreshCw className="h-3.5 w-3.5" /> 渲染 /prompt 请求体
        </button>
        <span className="text-[11.5px] text-text-4">
          按当前项目上下文（{proto.ctx.aspect} · {proto.ctx.resolution} · {proto.ctx.durationSeconds}s）填槽位
        </span>
      </div>
      {open && (
        <>
          <ul className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-[11.5px] text-text-3 md:grid-cols-3">
            <li>
              尺寸 <span className={`${MONO_CLS} text-text`}>{values.width ?? "—"}×{values.height ?? "—"}</span>
              <span className="text-text-4"> ← 短边 {values.short}，对齐 {values.roundTo}</span>
            </li>
            <li>
              帧数 <span className={`${MONO_CLS} text-text`}>{values.frames ?? "—"}</span>
              <span className="text-text-4"> ← {proto.ctx.durationSeconds}s × {values.fps}fps = {values.rawFrames}，对齐 {values.step}n+1</span>
            </li>
            <li>
              种子 <span className={`${MONO_CLS} text-text`}>{values.seed ?? "—"}</span>
              <span className="text-text-4"> ← {ep.bindings.seed.policy}</span>
            </li>
          </ul>
          <pre className="max-h-72 overflow-auto rounded-[8px] border border-hairline-soft bg-bg-grad-a/40 p-3 font-mono text-[11px] leading-[1.55] text-text-2">
            {`POST ${proto.provider.baseUrl}/prompt\n${headers.join("\n")}\n\n${JSON.stringify(body, null, 2)}`}
          </pre>
        </>
      )}
    </div>
  );
}

export function TrialRunBlock({ proto }: { proto: ComfyProto }) {
  const [simulateFail, setSimulateFail] = useState(false);
  const t = proto.trial;
  const busy = t.phase === "uploading" || t.phase === "queued" || t.phase === "running";
  const steps: { key: typeof t.phase; label: string }[] = [
    { key: "uploading", label: "上传素材 /upload/image" },
    { key: "queued", label: "已提交 · 排队中" },
    { key: "running", label: "运行中" },
    { key: "done", label: "取回产物 /view" },
  ];
  const idx = steps.findIndex((s) => s.key === t.phase);
  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className={ACCENT_BTN_SM_CLS} style={ACCENT_BUTTON_STYLE} disabled={busy} onClick={() => proto.runTrial({ simulateMissingModel: simulateFail })}>
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />} 真实提交一次
        </button>
        <span className="text-[11.5px] text-warm-bright/90">会占用这台机器的 GPU，费用记 0</span>
        <label className="ml-auto inline-flex items-center gap-1.5 text-[11px] text-text-4">
          <input type="checkbox" checked={simulateFail} onChange={(e) => setSimulateFail(e.target.checked)} /> 原型：模拟缺模型失败
        </label>
        {t.phase !== "idle" && !busy && (
          <button type="button" className={`${GHOST_BTN_CLS} px-2 py-1 text-[11px]`} onClick={proto.resetTrial}>
            清除
          </button>
        )}
      </div>
      {t.phase !== "idle" && (
        <ol className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[11.5px]">
          {steps.map((s, i) => {
            const state = t.phase === "failed" ? (i === 0 ? "done" : "skip") : i < idx || t.phase === "done" ? "done" : i === idx ? "now" : "todo";
            return (
              <li key={s.key} className="inline-flex items-center gap-1.5">
                <span className={`h-1.5 w-1.5 rounded-full ${state === "done" ? "bg-good" : state === "now" ? "bg-accent-2 animate-pulse" : "bg-hairline-strong"}`} />
                <span className={state === "todo" || state === "skip" ? "text-text-4" : "text-text-2"}>{s.label}</span>
                {i < steps.length - 1 && <ChevronRight className="h-3 w-3 text-text-4" />}
              </li>
            );
          })}
          {t.promptId && <li className={`${MONO_CLS} ml-2 text-text-4`}>prompt_id {t.promptId.slice(0, 8)}…</li>}
        </ol>
      )}
      {t.phase === "running" && <p className="text-[11.5px] text-text-4">不显示假进度，也不显示远端队列位次；超时前一直等。</p>}
      {t.phase === "done" && t.artifact && (
        <div className="flex items-center gap-3 rounded-[8px] border border-good/35 bg-good/10 p-3">
          <div className="grid h-14 w-24 place-items-center rounded-[6px] border border-hairline bg-bg-grad-a/70 font-mono text-[10px] text-text-4">mp4</div>
          <div className="min-w-0 text-[12px]">
            <div className="text-text">
              <span className={MONO_CLS}>{t.artifact.filename}</span> · {t.artifact.size}
            </div>
            <div className="text-text-3">{t.artifact.duration} · 执行 {((t.elapsedMs ?? 0) / 1000).toFixed(1)}s · 已按扩展名识别为视频</div>
          </div>
        </div>
      )}
      {t.phase === "failed" && t.error && (
        <div className="rounded-[8px] border border-danger/45 bg-danger/10 p-3 text-[12px]">
          <div className="text-danger">
            <span className={MONO_CLS}>{t.error.code}</span> · {t.error.message}
          </div>
          {t.error.detail && <div className={`${MONO_CLS} mt-1 text-text-3`}>{t.error.detail}</div>}
          <div className="mt-1.5 text-text-4">这类错误指向「配置供应商」：去 ComfyUI 机器上补模型，或改 workflow 里的文件名后重新导入。</div>
        </div>
      )}
    </div>
  );
}

// ---------- 小工具 ----------

export function Disclosure({ title, defaultOpen, children, trailing }: { title: ReactNode; defaultOpen?: boolean; children: ReactNode; trailing?: ReactNode }) {
  const [open, setOpen] = useState(!!defaultOpen);
  return (
    <div>
      <div className="flex items-center gap-2">
        <button type="button" className="inline-flex items-center gap-1 text-[12.5px] text-text-2 hover:text-text" onClick={() => setOpen((o) => !o)}>
          {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          {title}
        </button>
        {trailing && <span className="ml-auto">{trailing}</span>}
      </div>
      {open && <div className="mt-2">{children}</div>}
    </div>
  );
}

export function bindingSummary(ep: WorkflowEndpoint) {
  const all = SLOTS.map((s) => ep.bindings[s.id]);
  return {
    bound: all.filter((b) => b.chosen).length,
    ambiguous: all.filter((b) => b.status === "ambiguous").length,
    missing: all.filter((b) => b.status === "missing").length,
    unsupported: all.filter((b) => b.status === "unsupported").length,
  };
}
