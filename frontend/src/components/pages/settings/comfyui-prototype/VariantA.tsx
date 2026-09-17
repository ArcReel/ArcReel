// PROTOTYPE — 一次性代码，不进 main。
// 变体 A「归位」：完全沿用现有设置页的信息架构——左侧列表导航 + 右侧详情。
// 服务是「供应商」列表里的一项，workflow 是「调用端点」列表里的一项；导入走弹窗；
// 绑定表是端点详情里的一个分节表格，按行展开候选。赌的是「用户已经会用现有两个小节」。
import { FileJson2, Server, Upload, Waypoints } from "lucide-react";
import { useState } from "react";

import { ACCENT_BTN_SM_CLS, ACCENT_BUTTON_STYLE, CARD_STYLE, GHOST_BTN_CLS, INPUT_CLS } from "@/components/ui/darkroom-tokens";
import { GlassModal } from "@/components/ui/GlassModal";
import { ModalCloseButton } from "@/components/ui/ModalCloseButton";

import { savePendingReasons, SLOTS, type ComfyProto, type WorkflowEndpoint } from "./fixtures";
import {
  bindingSummary,
  CandidateList,
  ConnectivityBadge,
  ImportPanel,
  KICKER_ACCENT_CLS,
  KICKER_CLS,
  LABEL_CLS,
  ManualPicker,
  ModelRowsEditor,
  MONO_CLS,
  PreviewRequestBlock,
  ProtocolRow,
  ProviderFields,
  SlotActions,
  SlotExtras,
  SlotTag,
  StatusPill,
  TargetLabel,
  TrialRunBlock,
} from "./pieces";

export const VARIANT_A_NAME = "归位：沿用两栏列表 + 详情";

/** 「endpoint」始终指当前 draft：openEndpoint 会把被点的端点载入 draft。 */
type Sel = "provider" | "endpoint";

export function VariantA({ proto }: { proto: ComfyProto }) {
  const [sel, setSel] = useState<Sel>("provider");
  const [importOpen, setImportOpen] = useState(false);

  const entries: WorkflowEndpoint[] = [...proto.endpoints];
  if (proto.draft && !proto.draft.saved && !entries.some((e) => e.key === proto.draft!.key)) entries.push(proto.draft);
  const current = sel === "endpoint" ? proto.draft : null;

  return (
    <div className="flex min-h-full">
      <nav className="sticky top-0 max-h-screen w-60 shrink-0 self-start overflow-y-auto border-r border-hairline-soft px-3 py-5">
        <div className="mb-3 flex items-center gap-1.5 px-1">
          <button type="button" className={`${GHOST_BTN_CLS} flex-1 justify-center`} onClick={() => setImportOpen(true)}>
            <Upload className="h-3.5 w-3.5" /> 导入 workflow
          </button>
        </div>
        <div className={`${KICKER_CLS} px-2 pb-1.5`}>Providers · ComfyUI</div>
        <button
          type="button"
          onClick={() => setSel("provider")}
          className={`mb-3 flex w-full items-center gap-2 rounded-[7px] px-2 py-1.5 text-left text-[12.5px] ${sel === "provider" ? "bg-accent-dim text-text" : "text-text-2 hover:bg-bg-grad-a/60"}`}
        >
          <Server className="h-3.5 w-3.5 shrink-0 text-text-4" />
          <span className="min-w-0 flex-1 truncate">{proto.provider.name}</span>
          <span className={`h-1.5 w-1.5 rounded-full ${proto.provider.check.status === "ok" ? "bg-good" : proto.provider.check.status === "fail" ? "bg-danger" : "bg-hairline-strong"}`} />
        </button>
        <div className={`${KICKER_CLS} px-2 pb-1.5`}>Workflow endpoints</div>
        {entries.length === 0 && <p className="px-2 text-[11.5px] text-text-4">还没有 workflow 端点。点上方「导入 workflow」。</p>}
        {entries.map((e) => {
          const active = sel === "endpoint" && proto.draft?.key === e.key;
          const refs = proto.models.filter((m) => m.endpointKey === e.key).length;
          return (
            <button
              key={e.key}
              type="button"
              onClick={() => {
                proto.openEndpoint(e.key);
                setSel("endpoint");
              }}
              className={`flex w-full items-center gap-2 rounded-[7px] px-2 py-1.5 text-left text-[12.5px] ${active ? "bg-accent-dim text-text" : "text-text-2 hover:bg-bg-grad-a/60"}`}
            >
              <Waypoints className="h-3.5 w-3.5 shrink-0 text-text-4" />
              <span className={`min-w-0 flex-1 truncate ${e.saved ? "" : "italic"}`}>{e.name}</span>
              {!e.saved ? <span className="font-mono text-[9.5px] text-warn">未保存</span> : refs > 0 ? <span className="font-mono text-[10px] text-text-4">{refs}</span> : null}
            </button>
          );
        })}
      </nav>

      <div className="min-w-0 flex-1 px-8 py-6">
        {sel === "provider" ? <ProviderDetail proto={proto} onGoImport={() => setImportOpen(true)} /> : current ? <EndpointDetail ep={current} proto={proto} onReimport={() => setImportOpen(true)} /> : <p className="text-text-4">该端点已不存在。</p>}
      </div>

      <GlassModal open={importOpen} onClose={() => setImportOpen(false)} ariaLabel="导入 workflow" widthClassName="w-full max-w-2xl">
        <div className="px-6 py-5">
          <div className="flex items-start justify-between">
            <div>
              <div className={KICKER_ACCENT_CLS}>Import · workflow endpoint</div>
              <h3 className="mt-1 text-[15px] font-medium text-text">导入 workflow</h3>
              <p className="mt-0.5 text-[12px] text-text-3">接受两种文件：ComfyUI「Export (API)」导出的 workflow，或一份 ArcReel workflow 端点定义。UI 格式会被拒绝。</p>
            </div>
            <ModalCloseButton onClick={() => setImportOpen(false)} />
          </div>
          <div className="mt-4">
            <ImportPanel
              proto={proto}
              onImported={() => {
                setImportOpen(false);
                setSel("endpoint");
              }}
            />
          </div>
        </div>
      </GlassModal>
    </div>
  );
}

function Section({ kicker, title, description, children, trailing }: { kicker: string; title: string; description?: string; children: React.ReactNode; trailing?: React.ReactNode }) {
  return (
    <section>
      <div className="mb-2.5 flex items-start justify-between gap-3">
        <div>
          <div className={KICKER_ACCENT_CLS}>{kicker}</div>
          <h3 className="mt-0.5 text-[14px] font-medium text-text">{title}</h3>
          {description && <p className="mt-0.5 text-[12px] leading-[1.55] text-text-3">{description}</p>}
        </div>
        {trailing}
      </div>
      <div className="rounded-[10px] border border-hairline p-4" style={CARD_STYLE}>
        {children}
      </div>
    </section>
  );
}

function ProviderDetail({ proto, onGoImport }: { proto: ComfyProto; onGoImport: () => void }) {
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <div className={KICKER_ACCENT_CLS}>Custom provider · comfyui</div>
        <h2 className="mt-1 font-editorial text-[22px] text-text">{proto.provider.name}</h2>
        <p className="mt-1 text-[12.5px] text-text-3">一台自建 ComfyUI 服务。它的模型行只能挂接 workflow 端点；图像与视频模型行可以混挂在同一台机器下。</p>
      </div>
      <Section kicker="Connection" title="连接">
        <ProviderFields proto={proto} />
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button type="button" className={GHOST_BTN_CLS} onClick={proto.checkConnectivity}>
            测试连接
          </button>
          <ConnectivityBadge proto={proto} />
        </div>
        <div className="mt-3 border-t border-hairline-soft pt-3">
          <ProtocolRow />
        </div>
      </Section>
      <Section
        kicker="Models"
        title="模型行"
        description="项目里看到的名字。每行挂接一份 workflow 端点；同一份端点可以挂多行（比如不同单价）。"
        trailing={
          proto.endpoints.length === 0 ? (
            <button type="button" className={GHOST_BTN_CLS} onClick={onGoImport}>
              <Upload className="h-3.5 w-3.5" /> 先导入 workflow
            </button>
          ) : null
        }
      >
        <ModelRowsEditor proto={proto} />
      </Section>
      <Section kicker="Concurrency" title="并发">
        <div className="grid grid-cols-3 gap-3">
          {["图像", "视频", "音频"].map((k) => (
            <label key={k} className="block">
              <span className={LABEL_CLS}>{k}并发上限</span>
              <input className={INPUT_CLS} defaultValue={1} type="number" min={1} />
            </label>
          ))}
        </div>
        <p className="mt-2 text-[11.5px] text-text-4">comfyui 协议默认 1：一台机器同时只跑一条，多了只是在 ComfyUI 侧排队。</p>
      </Section>
    </div>
  );
}

function EndpointDetail({ ep, proto, onReimport }: { ep: WorkflowEndpoint; proto: ComfyProto; onReimport: () => void }) {
  const reasons = savePendingReasons(ep.bindings);
  const sum = bindingSummary(ep);
  const [expanded, setExpanded] = useState<string | null>(null);
  const classTypes = Array.from(new Set(ep.nodes.map((n) => n.class_type)));
  const refs = proto.models.filter((m) => m.endpointKey === ep.key).length;

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className={KICKER_ACCENT_CLS}>Workflow endpoint</div>
          <div className="mt-1 flex items-center gap-2.5">
            <input className="min-w-0 flex-1 bg-transparent font-editorial text-[22px] text-text outline-none focus:border-b focus:border-accent/50" value={ep.name} onChange={(e) => proto.setDraftMeta({ name: e.target.value })} aria-label="端点名称" />
            <span className="shrink-0 rounded-[5px] border border-accent/35 bg-accent-dim px-1.5 py-0.5 font-mono text-[9.5px] font-bold uppercase tracking-[0.1em] text-accent-2">comfyui</span>
            <span className="shrink-0 rounded-[5px] border border-hairline px-1.5 py-0.5 font-mono text-[9.5px] font-bold uppercase tracking-[0.1em] text-text-3">{ep.mediaType}</span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2.5 text-[12px] text-text-3">
            <span className={MONO_CLS}>{ep.key}</span>
            <span aria-hidden>·</span>
            <span>
              来自 <span className={MONO_CLS}>{ep.fileName}</span>
            </span>
            <span aria-hidden>·</span>
            <span>{ep.nodes.length} 个节点</span>
            <span aria-hidden>·</span>
            <span>{refs} 个模型行引用</span>
          </div>
        </div>
        <button type="button" className={GHOST_BTN_CLS} onClick={onReimport}>
          <FileJson2 className="h-3.5 w-3.5" /> 重新导入
        </button>
        <button type="button" className={GHOST_BTN_CLS}>
          导出定义
        </button>
      </div>

      <Section
        kicker="Bindings"
        title="绑定表"
        description="每个槽位对应 workflow 里的一个节点字段。自动识别的可以改；并列候选必须你来选；没有的槽位标为不支持即可。"
        trailing={
          <span className="text-[11.5px] text-text-4">
            已绑定 {sum.bound} · 需选择 {sum.ambiguous} · 未找到 {sum.missing} · 不支持 {sum.unsupported}
          </span>
        }
      >
        <div className="divide-y divide-hairline-soft">
          {SLOTS.map((slot) => {
            const b = ep.bindings[slot.id];
            const open = expanded === slot.id || b.status === "ambiguous";
            return (
              <div key={slot.id} className="py-2.5">
                <div className="grid grid-cols-[180px_100px_1fr_auto] items-center gap-3">
                  <SlotTag id={slot.id} />
                  <StatusPill status={b.status} required={slot.required} />
                  <div className="min-w-0 truncate text-[12px]">
                    {b.chosen ? <TargetLabel c={b.chosen} /> : b.status === "unsupported" ? <span className="text-text-4">此 workflow 没有这个入口</span> : b.status === "ambiguous" ? <span className="text-warn">{b.candidates.length} 个候选得分相同</span> : <span className="text-text-4">{slot.hint}</span>}
                  </div>
                  <button type="button" className="text-[11.5px] text-text-3 hover:text-text" onClick={() => setExpanded(open && b.status !== "ambiguous" ? null : slot.id)}>
                    {open ? "收起" : b.candidates.length > 0 ? `候选 ${b.candidates.length}` : "手选"}
                  </button>
                </div>
                {open && (
                  <div className="ml-[192px] mt-2 space-y-2">
                    {b.candidates.length > 0 && <CandidateList slot={slot.id} binding={b} proto={proto} />}
                    <div className="flex flex-wrap items-center gap-3">
                      <div className="w-72">
                        <ManualPicker slot={slot.id} ep={ep} proto={proto} compact />
                      </div>
                      <SlotExtras slot={slot.id} binding={b} proto={proto} />
                      <span className="ml-auto">
                        <SlotActions slot={slot.id} binding={b} proto={proto} />
                      </span>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-hairline-soft pt-3">
          <button type="button" className={ACCENT_BTN_SM_CLS} style={ACCENT_BUTTON_STYLE} disabled={reasons.length > 0 || ep.saved} onClick={() => proto.saveDraft()}>
            {ep.saved ? "已保存" : "保存 workflow 端点"}
          </button>
          {reasons.length > 0 ? (
            <ul className="text-[11.5px] text-danger">
              {reasons.map((r) => (
                <li key={r}>· {r}</li>
              ))}
            </ul>
          ) : (
            !ep.saved && <span className="text-[11.5px] text-text-4">可以保存了。保存后到供应商页给它挂一行模型。</span>
          )}
        </div>
      </Section>

      <Section kicker="Definition" title="定义">
        <div className="grid grid-cols-2 gap-4">
          <label className="block">
            <span className={LABEL_CLS}>媒体类型</span>
            <div className="flex gap-2">
              {(["video", "image"] as const).map((m) => (
                <button key={m} type="button" onClick={() => proto.setDraftMeta({ mediaType: m })} className={`rounded-[7px] border px-3 py-1 font-mono text-[11.5px] ${ep.mediaType === m ? "border-accent/45 bg-accent-dim text-accent-2" : "border-hairline-soft text-text-3"}`}>
                  {m}
                </button>
              ))}
            </div>
          </label>
          <div>
            <span className={LABEL_CLS}>凭据注入（auth 节）</span>
            <pre className={`${MONO_CLS} rounded-[7px] border border-hairline-soft bg-bg-grad-a/40 p-2 text-text-2`}>{`headers:\n  Authorization: Bearer {{api_key}}`}</pre>
            <p className="mt-1 text-[11px] text-text-4">供应商 API Key 留空时整节不渲染。</p>
          </div>
        </div>
      </Section>

      <Section kicker="Workflow" title="Workflow 本体" description="原样内嵌在定义里，是提交时的底稿。要改固定值（模型文件名、默认负向），改完在 ComfyUI 重新导出再导入。">
        <div className="flex flex-wrap gap-1.5">
          {classTypes.map((c) => (
            <span key={c} className={`${MONO_CLS} rounded-[5px] border border-hairline-soft px-1.5 py-0.5 text-text-3`}>
              {c} <span className="text-text-4">×{ep.nodes.filter((n) => n.class_type === c).length}</span>
            </span>
          ))}
        </div>
      </Section>

      <Section kicker="Test" title="端点测试" description="workflow 端点只有两种测试：预览渲染后的请求体，或真的提交一次。没有「验证响应」——产物提取是固定代码，没有用户可配的路径。">
        <div className="space-y-4">
          <div className="rounded-[8px] border border-hairline-soft bg-bg-grad-a/30 p-3.5">
            <div className="text-[13px] font-medium text-text">预览请求</div>
            <p className="mb-2.5 mt-0.5 text-[12px] text-text-3">看 ArcReel 会把什么写进 workflow。</p>
            <PreviewRequestBlock ep={ep} proto={proto} />
          </div>
          <div className="rounded-[8px] border border-hairline-soft bg-bg-grad-a/30 p-3.5">
            <div className="flex items-center gap-2">
              <span className="text-[13px] font-medium text-text">测试连接</span>
              <span className="text-[11px] text-warm-bright/90">占用 GPU</span>
            </div>
            <p className="mb-2.5 mt-0.5 text-[12px] text-text-3">真实提交并轮询到终态，展示取回的产物。</p>
            <TrialRunBlock proto={proto} />
          </div>
        </div>
      </Section>
    </div>
  );
}
