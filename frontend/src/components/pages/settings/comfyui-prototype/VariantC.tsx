// PROTOTYPE — 一次性代码，不进 main。
// 变体 C「检查器」：以 workflow 为中心的双栏工作台。左栏是节点清单（每个节点列出可写字段），
// 右栏是槽位轨道；先点槽位、再点左边某个字段即绑定，候选在节点清单里就地高亮并显示分数。
// 服务与导入压成顶部一条状态带；模型行与测试是右栏的两个 tab。
// 赌的是「用户认识自己的 workflow，看到节点比看到槽位表格更快定位」。
import { Link2, Play, Server, Upload } from "lucide-react";
import { useMemo, useState } from "react";

import { ACCENT_BTN_SM_CLS, ACCENT_BUTTON_STYLE, GHOST_BTN_CLS, INPUT_CLS } from "@/components/ui/darkroom-tokens";

import { isLink, savePendingReasons, SLOT_BY_ID, SLOTS, writableInputs, type Candidate, type ComfyProto, type SlotId, type WfNode, type WorkflowEndpoint } from "./fixtures";
import {
  ConnectivityBadge,
  ImportPanel,
  KICKER_ACCENT_CLS,
  KICKER_CLS,
  ModelRowsEditor,
  MONO_CLS,
  PreviewRequestBlock,
  ScoreBar,
  SlotActions,
  SlotExtras,
  StatusPill,
  TrialRunBlock,
} from "./pieces";

export const VARIANT_C_NAME = "检查器：节点清单 + 槽位轨道";

type Tab = "bindings" | "models" | "test";

export function VariantC({ proto }: { proto: ComfyProto }) {
  const ep = proto.draft;
  const [selSlot, setSelSlot] = useState<SlotId>("prompt");
  const [tab, setTab] = useState<Tab>("bindings");
  const [importing, setImporting] = useState(!ep);
  const [query, setQuery] = useState("");

  return (
    <div className="flex h-full min-h-0 flex-col">
      <TopStrip proto={proto} ep={ep} onImport={() => setImporting((v) => !v)} importing={importing} />
      {importing || !ep ? (
        <div className="mx-auto w-full max-w-3xl px-8 py-8">
          <div className={KICKER_ACCENT_CLS}>Import</div>
          <h3 className="mt-1 text-[16px] font-medium text-text">{ep ? "替换 workflow" : "从一份 workflow 开始"}</h3>
          <p className="mb-4 mt-0.5 text-[12.5px] text-text-3">{ep ? "重导入会按 节点 ID + class_type + 标题 逐条重匹配已有绑定，匹配不上的槽位重新推断并标「需确认」。" : "导入后左边出现节点清单，右边出现 11 个槽位。先点槽位，再点节点上的字段。"}</p>
          <ImportPanel proto={proto} onImported={() => setImporting(false)} />
        </div>
      ) : (
        <div className="flex min-h-0 flex-1">
          <div className="min-w-0 flex-1 overflow-y-auto border-r border-hairline-soft px-5 py-4">
            <div className="mb-3 flex items-center gap-3">
              <div>
                <div className={KICKER_CLS}>Workflow · {ep.nodes.length} nodes</div>
                <div className="text-[13px] text-text-2">
                  正在为 <span className={`${MONO_CLS} text-accent-2`}>{selSlot}</span> <span className="text-text-3">{SLOT_BY_ID[selSlot].label}</span> 选目标 — 点下面任一字段即绑定
                </div>
              </div>
              <input className={`${INPUT_CLS} ml-auto w-56 py-1 text-[12px]`} placeholder="过滤 class_type / 标题 / 字段" value={query} onChange={(e) => setQuery(e.target.value)} />
            </div>
            <NodeList ep={ep} proto={proto} selSlot={selSlot} query={query} onPickSlot={setSelSlot} />
          </div>
          <aside className="flex w-[380px] shrink-0 flex-col">
            <div className="flex border-b border-hairline-soft px-2 pt-2">
              {(
                [
                  ["bindings", "绑定"],
                  ["models", "模型行"],
                  ["test", "测试"],
                ] as [Tab, string][]
              ).map(([k, label]) => (
                <button key={k} type="button" onClick={() => setTab(k)} className={`px-3 pb-2 pt-1 text-[12.5px] ${tab === k ? "border-b-2 border-accent-2 text-text" : "text-text-3 hover:text-text"}`}>
                  {label}
                </button>
              ))}
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-3">
              {tab === "bindings" && <SlotRail ep={ep} proto={proto} selSlot={selSlot} onSelect={setSelSlot} />}
              {tab === "models" && (
                <div className="space-y-3">
                  <p className="text-[12px] text-text-3">这台服务下的模型行。只能挂 workflow 端点。</p>
                  <ModelRowsEditor proto={proto} />
                </div>
              )}
              {tab === "test" && (
                <div className="space-y-5">
                  <div>
                    <div className="mb-1.5 text-[12.5px] font-medium text-text">预览请求</div>
                    <PreviewRequestBlock ep={ep} proto={proto} />
                  </div>
                  <div>
                    <div className="mb-1.5 text-[12.5px] font-medium text-text">真实提交</div>
                    <TrialRunBlock proto={proto} />
                  </div>
                </div>
              )}
            </div>
            <SaveBar ep={ep} proto={proto} />
          </aside>
        </div>
      )}
    </div>
  );
}

function TopStrip({ proto, ep, onImport, importing }: { proto: ComfyProto; ep: WorkflowEndpoint | null; onImport: () => void; importing: boolean }) {
  const [editing, setEditing] = useState(false);
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-hairline-soft bg-bg-grad-a/30 px-5 py-2.5">
      <Server className="h-4 w-4 text-text-4" />
      {editing ? (
        <>
          <input className={`${INPUT_CLS} w-40 py-1 text-[12px]`} value={proto.provider.name} onChange={(e) => proto.setProvider({ name: e.target.value })} aria-label="显示名" />
          <input className={`${INPUT_CLS} ${MONO_CLS} w-64 py-1`} value={proto.provider.baseUrl} onChange={(e) => proto.setProvider({ baseUrl: e.target.value })} aria-label="服务地址" />
          <input className={`${INPUT_CLS} ${MONO_CLS} w-40 py-1`} type="password" placeholder="API Key（可空）" value={proto.provider.apiKey} onChange={(e) => proto.setProvider({ apiKey: e.target.value })} aria-label="API Key" />
          <button type="button" className={`${GHOST_BTN_CLS} py-1`} onClick={() => setEditing(false)}>
            收起
          </button>
        </>
      ) : (
        <button type="button" className="text-left" onClick={() => setEditing(true)} title="点击编辑服务">
          <span className="text-[13px] text-text">{proto.provider.name}</span>
          <span className={`${MONO_CLS} ml-2 text-text-3`}>{proto.provider.baseUrl}</span>
          <span className="ml-2 rounded-[4px] border border-hairline-soft px-1 font-mono text-[9.5px] uppercase tracking-[0.1em] text-text-4">comfyui</span>
        </button>
      )}
      <button type="button" className={`${GHOST_BTN_CLS} py-1`} onClick={proto.checkConnectivity}>
        测试连接
      </button>
      <ConnectivityBadge proto={proto} />
      <span className="ml-auto inline-flex items-center gap-2">
        {ep && (
          <span className="text-[12px] text-text-3">
            <span className="text-text">{ep.name}</span> <span className={MONO_CLS}>{ep.key}</span> · {ep.mediaType}
            {!ep.saved && <span className="ml-1.5 font-mono text-[10px] text-warn">未保存</span>}
          </span>
        )}
        <button type="button" className={`${GHOST_BTN_CLS} py-1`} onClick={onImport}>
          <Upload className="h-3.5 w-3.5" /> {ep ? (importing ? "取消替换" : "替换 workflow") : "导入 workflow"}
        </button>
      </span>
    </div>
  );
}

function boundSlotsOf(ep: WorkflowEndpoint): Map<string, SlotId[]> {
  const m = new Map<string, SlotId[]>();
  for (const s of SLOTS) {
    const c = ep.bindings[s.id].chosen;
    if (!c) continue;
    const key = `${c.node}.${c.input ?? ""}`;
    m.set(key, [...(m.get(key) ?? []), s.id]);
  }
  return m;
}

function NodeList({ ep, proto, selSlot, query, onPickSlot }: { ep: WorkflowEndpoint; proto: ComfyProto; selSlot: SlotId; query: string; onPickSlot: (s: SlotId) => void }) {
  const binding = ep.bindings[selSlot];
  const candMap = useMemo(() => {
    const m = new Map<string, Candidate>();
    for (const c of binding.candidates) m.set(`${c.node}.${c.input ?? ""}`, c);
    return m;
  }, [binding]);
  const max = binding.candidates[0]?.score ?? 1;
  const bound = useMemo(() => boundSlotsOf(ep), [ep]);
  const isOutput = selSlot === "output";

  const nodes = useMemo(() => {
    const q = query.trim().toLowerCase();
    const ordered = [...ep.nodes].sort((a, b) => scoreNode(b, candMap) - scoreNode(a, candMap) || Number(a.id) - Number(b.id));
    if (!q) return ordered;
    return ordered.filter((n) => n.class_type.toLowerCase().includes(q) || n.title.toLowerCase().includes(q) || Object.keys(n.inputs).some((k) => k.includes(q)));
  }, [ep.nodes, query, candMap]);

  return (
    <div className="space-y-2">
      {nodes.map((n) => {
        const nodeCand = isOutput ? candMap.get(`${n.id}.`) : undefined;
        const nodeChosen = isOutput && binding.chosen?.node === n.id;
        const links = Object.entries(n.inputs).filter(([, v]) => isLink(v));
        return (
          <div key={n.id} className={`rounded-[9px] border ${nodeCand || nodeChosen ? "border-accent/40" : "border-hairline-soft"} bg-bg-grad-a/25`}>
            <div className="flex items-center gap-2 px-3 py-2">
              <span className={`${MONO_CLS} text-text-4`}>#{n.id}</span>
              <span className={`${MONO_CLS} text-text`}>{n.class_type}</span>
              {n.title && n.title !== n.class_type && <span className="truncate text-[11.5px] text-text-4">“{n.title}”</span>}
              <span className="ml-auto inline-flex items-center gap-2">
                {links.length > 0 && (
                  <span className="text-[10.5px] text-text-4" title={links.map(([k, v]) => `${k} ← #${(v as [string, number])[0]}`).join("\n")}>
                    <Link2 className="mr-0.5 inline h-3 w-3" />
                    {links.length} 条连线
                  </span>
                )}
                {isOutput && (
                  <button
                    type="button"
                    onClick={() => proto.choose("output", nodeCand ?? { node: n.id, class_type: n.class_type, title: n.title, score: 0, signals: ["手动指定"] })}
                    className={`inline-flex items-center gap-1.5 rounded-[6px] border px-2 py-0.5 text-[11px] ${nodeChosen ? "border-accent/50 bg-accent-dim text-accent-2" : nodeCand ? "border-warn/50 text-warn" : "border-hairline-soft text-text-4 hover:text-text"}`}
                  >
                    {nodeChosen ? "产物节点 ✓" : nodeCand ? "候选 · 设为产物" : "设为产物"}
                    {nodeCand && <ScoreBar score={nodeCand.score} max={max} />}
                  </button>
                )}
              </span>
            </div>
            {!isOutput && (
              <div className="divide-y divide-hairline-soft/60 border-t border-hairline-soft/60">
                {writableInputs(n).map(([k, v]) => {
                  const key = `${n.id}.${k}`;
                  const cand = candMap.get(key);
                  const chosenHere = binding.chosen?.node === n.id && binding.chosen?.input === k;
                  const tags = bound.get(key) ?? [];
                  return (
                    <button
                      key={k}
                      type="button"
                      onClick={() => proto.choose(selSlot, cand ?? { node: n.id, input: k, class_type: n.class_type, title: n.title, score: 0, signals: ["手动指定"] })}
                      className={`grid w-full grid-cols-[150px_1fr_auto] items-center gap-3 px-3 py-1 text-left text-[12px] transition-colors hover:bg-accent-dim/60 ${chosenHere ? "bg-accent-dim" : cand ? "bg-warn/5" : ""}`}
                    >
                      <span className={`${MONO_CLS} ${chosenHere ? "text-accent-2" : "text-text-2"}`}>.{k}</span>
                      <span className={`${MONO_CLS} truncate text-text-4`}>{typeof v === "string" ? `"${v.length > 60 ? v.slice(0, 60) + "…" : v}"` : JSON.stringify(v)}</span>
                      <span className="inline-flex items-center gap-1.5">
                        {tags.map((t) => (
                          <button
                            key={t}
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              onPickSlot(t);
                            }}
                            className={`rounded-full border px-1.5 py-px font-mono text-[9.5px] ${t === selSlot ? "border-accent/50 bg-accent-dim text-accent-2" : "border-hairline text-text-3"}`}
                            title={`已绑定到 ${t}，点击切换到该槽位`}
                          >
                            {t}
                          </button>
                        ))}
                        {cand && !chosenHere && <ScoreBar score={cand.score} max={max} />}
                        {cand && <span className="text-[10.5px] text-text-4" title={cand.signals.join(" · ")}>{cand.signals[0]}</span>}
                      </span>
                    </button>
                  );
                })}
                {writableInputs(n).length === 0 && <div className="px-3 py-1 text-[11px] text-text-4">没有可写字段（全是连线）</div>}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function scoreNode(n: WfNode, candMap: Map<string, Candidate>): number {
  let best = -1;
  for (const [k, c] of candMap) if (k.startsWith(`${n.id}.`)) best = Math.max(best, c.score);
  return best;
}

function SlotRail({ ep, proto, selSlot, onSelect }: { ep: WorkflowEndpoint; proto: ComfyProto; selSlot: SlotId; onSelect: (s: SlotId) => void }) {
  const groups = Array.from(new Set(SLOTS.map((s) => s.group)));
  return (
    <div className="space-y-3">
      {groups.map((g) => (
        <div key={g}>
          <div className={`${KICKER_CLS} mb-1 px-1`}>{g}</div>
          <div className="space-y-1">
            {SLOTS.filter((s) => s.group === g).map((s) => {
              const b = ep.bindings[s.id];
              const active = s.id === selSlot;
              return (
                <div key={s.id} className={`rounded-[8px] border ${active ? "border-accent/50 bg-accent-dim/60" : "border-hairline-soft"}`}>
                  <button type="button" onClick={() => onSelect(s.id)} className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left">
                    <span className={`h-2 w-2 shrink-0 rounded-full ${b.chosen ? "bg-good" : b.status === "ambiguous" || (s.required && !b.chosen) ? "bg-danger" : b.status === "unsupported" ? "bg-hairline-strong" : "bg-warn"}`} />
                    <span className={`${MONO_CLS} ${active ? "text-accent-2" : "text-text"}`}>{s.id}</span>
                    <span className="text-[11.5px] text-text-3">{s.label}</span>
                    {s.required && <span className="text-danger">*</span>}
                    <span className="ml-auto">
                      <StatusPill status={b.status} required={s.required} />
                    </span>
                  </button>
                  {active && (
                    <div className="space-y-2 border-t border-hairline-soft/60 px-2.5 py-2 text-[11.5px]">
                      <div className="text-text-3">
                        {b.chosen ? (
                          <>
                            → <span className={`${MONO_CLS} text-text`}>#{b.chosen.node} {b.chosen.class_type}{b.chosen.input ? `.${b.chosen.input}` : ""}</span>
                            <div className="mt-0.5 text-text-4">{b.chosen.signals.join(" · ")}</div>
                          </>
                        ) : b.status === "ambiguous" ? (
                          <span className="text-warn">{b.candidates.length} 个候选得分相同，左边已高亮，点一个。</span>
                        ) : b.status === "unsupported" ? (
                          "已标为不支持：项目里对应控件禁用。"
                        ) : (
                          <span>{s.hint}。左边没有高亮就说明推断没找到，可以直接点任一字段手动绑定。</span>
                        )}
                      </div>
                      <SlotExtras slot={s.id} binding={b} proto={proto} />
                      <SlotActions slot={s.id} binding={b} proto={proto} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

function SaveBar({ ep, proto }: { ep: WorkflowEndpoint; proto: ComfyProto }) {
  const reasons = savePendingReasons(ep.bindings);
  return (
    <div className="border-t border-hairline-soft bg-bg-grad-a/40 px-3 py-2.5">
      {reasons.length > 0 ? (
        <ul className="mb-2 space-y-0.5 text-[11.5px] text-danger">
          {reasons.map((r) => (
            <li key={r}>· {r}</li>
          ))}
        </ul>
      ) : (
        <p className="mb-2 text-[11.5px] text-text-4">{ep.saved ? "已保存。改动绑定后需要再次保存。" : "没有阻塞项，可以保存。"}</p>
      )}
      <div className="flex items-center gap-2">
        <input className={`${INPUT_CLS} flex-1 py-1 text-[12.5px]`} value={ep.name} onChange={(e) => proto.setDraftMeta({ name: e.target.value })} aria-label="端点名称" />
        <button type="button" className={ACCENT_BTN_SM_CLS} style={ACCENT_BUTTON_STYLE} disabled={reasons.length > 0 || ep.saved} onClick={() => proto.saveDraft()}>
          <Play className="h-3.5 w-3.5" /> {ep.saved ? "已保存" : "保存端点"}
        </button>
      </div>
    </div>
  );
}
