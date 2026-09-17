// PROTOTYPE — 一次性代码，不进 main。
// 变体 B「向导」：把「接入一台 ComfyUI」当成一条线性流程，一次只做一件事——
// 连接服务 → 导入 workflow → 确认绑定 → 挂接模型行 → 试跑。绑定确认不是表格，
// 而是按「需要你决定的先来」排序的卡片队列。赌的是「首次接入的人不认识供应商 / 端点 / 模型行这三层」。
import { ArrowLeft, ArrowRight, Check, CheckCircle2 } from "lucide-react";
import { useState } from "react";

import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE, CARD_STYLE, GHOST_BTN_CLS, GHOST_BTN_LG_CLS } from "@/components/ui/darkroom-tokens";

import { savePendingReasons, SLOTS, type ComfyProto, type SlotMeta, type WorkflowEndpoint } from "./fixtures";
import {
  CandidateList,
  ConnectivityBadge,
  ImportPanel,
  KICKER_ACCENT_CLS,
  KICKER_CLS,
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

export const VARIANT_B_NAME = "向导：一步一屏的接入流程";

const STEPS = ["连接服务", "导入 workflow", "确认绑定", "挂接模型行", "试跑"] as const;

export function VariantB({ proto }: { proto: ComfyProto }) {
  const [step, setStep] = useState(0);
  const ep = proto.draft;
  const reasons = ep ? savePendingReasons(ep.bindings) : ["尚未导入 workflow"];
  const canNext = [
    proto.provider.check.status === "ok",
    !!ep,
    !!ep && reasons.length === 0,
    proto.models.length > 0,
    true,
  ][step];

  const goNext = () => {
    if (step === 2 && ep && !ep.saved) proto.saveDraft();
    setStep((s) => Math.min(s + 1, STEPS.length - 1));
  };

  return (
    <div className="mx-auto max-w-3xl px-8 py-8">
      <div className={KICKER_ACCENT_CLS}>Connect · ComfyUI</div>
      <h2 className="mt-1 font-editorial text-[24px] text-text">接入一台 ComfyUI</h2>
      <p className="mt-1 text-[12.5px] text-text-3">五步。做完你会得到：一台服务、一份 workflow 端点、一行能在项目里选到的模型。</p>

      <ol className="mt-6 flex items-center gap-2">
        {STEPS.map((label, i) => {
          const done = i < step;
          const now = i === step;
          return (
            <li key={label} className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => i < step && setStep(i)}
                className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-[12px] ${now ? "border-accent/50 bg-accent-dim text-text" : done ? "border-good/40 text-text-2" : "border-hairline-soft text-text-4"}`}
              >
                <span className={`grid h-4 w-4 place-items-center rounded-full font-mono text-[10px] ${now ? "bg-accent-2 text-black" : done ? "bg-good text-black" : "bg-bg-grad-a text-text-4"}`}>{done ? <Check className="h-3 w-3" /> : i + 1}</span>
                {label}
              </button>
              {i < STEPS.length - 1 && <span className="h-px w-4 bg-hairline-soft" />}
            </li>
          );
        })}
      </ol>

      <div className="mt-6 rounded-[12px] border border-hairline p-6" style={CARD_STYLE}>
        {step === 0 && <StepConnect proto={proto} />}
        {step === 1 && <StepImport proto={proto} onImported={() => setStep(2)} />}
        {step === 2 && ep && <StepBindings proto={proto} ep={ep} />}
        {step === 3 && <StepModel proto={proto} />}
        {step === 4 && ep && <StepTrial proto={proto} ep={ep} />}
      </div>

      <div className="mt-4 flex items-center gap-3">
        <button type="button" className={GHOST_BTN_LG_CLS} disabled={step === 0} onClick={() => setStep((s) => s - 1)}>
          <ArrowLeft className="h-4 w-4" /> 上一步
        </button>
        <span className="flex-1 text-[11.5px] text-text-4">{!canNext && step < 4 ? NEXT_HINT[step](reasons) : ""}</span>
        {step < STEPS.length - 1 ? (
          <button type="button" className={ACCENT_BTN_CLS} style={ACCENT_BUTTON_STYLE} disabled={!canNext} onClick={goNext}>
            {step === 2 ? "保存并继续" : "下一步"} <ArrowRight className="h-4 w-4" />
          </button>
        ) : (
          <button type="button" className={ACCENT_BTN_CLS} style={ACCENT_BUTTON_STYLE}>
            完成，去项目里用
          </button>
        )}
      </div>
    </div>
  );
}

const NEXT_HINT: ((reasons: string[]) => string)[] = [
  () => "先测试一次连接，确认这台机器可达",
  () => "导入一份 API 格式 workflow 后才能继续",
  (r) => r[0] ?? "",
  () => "至少挂一行模型，项目里才选得到",
  () => "",
];

function StepHeader({ kicker, title, desc }: { kicker: string; title: string; desc: string }) {
  return (
    <div className="mb-4">
      <div className={KICKER_CLS}>{kicker}</div>
      <h3 className="mt-0.5 text-[16px] font-medium text-text">{title}</h3>
      <p className="mt-0.5 text-[12.5px] leading-[1.55] text-text-3">{desc}</p>
    </div>
  );
}

function StepConnect({ proto }: { proto: ComfyProto }) {
  return (
    <div>
      <StepHeader kicker="Step 1" title="这台 ComfyUI 在哪" desc="本机、内网、自有云主机都一样：只要 HTTP 可达。ArcReel 不假设共享文件系统。" />
      <ProviderFields proto={proto} />
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button type="button" className={GHOST_BTN_CLS} onClick={proto.checkConnectivity}>
          测试连接
        </button>
        <ConnectivityBadge proto={proto} />
      </div>
      <div className="mt-4 border-t border-hairline-soft pt-3">
        <ProtocolRow />
      </div>
    </div>
  );
}

function StepImport({ proto, onImported }: { proto: ComfyProto; onImported: () => void }) {
  return (
    <div>
      <StepHeader kicker="Step 2" title="导入 workflow" desc="在 ComfyUI 里打开你的 workflow，菜单 → Export (API)，把导出的 JSON 拖进来。ArcReel 会自动找出提示词、首帧、尺寸、产物等位置。" />
      <ImportPanel proto={proto} onImported={onImported} />
      {proto.draft && (
        <div className="mt-3 flex items-center gap-2 text-[12px] text-text-3">
          <CheckCircle2 className="h-4 w-4 text-good" /> 已导入 <span className={MONO_CLS}>{proto.draft.fileName}</span>，可以继续，也可以再导一份替换。
        </div>
      )}
    </div>
  );
}

function StepBindings({ proto, ep }: { proto: ComfyProto; ep: WorkflowEndpoint }) {
  const needs = SLOTS.filter((s) => ep.bindings[s.id].status === "ambiguous" || (ep.bindings[s.id].status === "missing" && s.required));
  const optionalMissing = SLOTS.filter((s) => ep.bindings[s.id].status === "missing" && !s.required);
  const auto = SLOTS.filter((s) => ep.bindings[s.id].status === "auto" || ep.bindings[s.id].status === "manual");
  const unsupported = SLOTS.filter((s) => ep.bindings[s.id].status === "unsupported");
  return (
    <div className="space-y-5">
      <StepHeader kicker="Step 3" title="确认绑定" desc="下面按「需要你决定的先来」排序。自动识别的一般不用动，展开看一眼就行。" />
      {needs.length > 0 && (
        <div>
          <div className="mb-2 text-[12.5px] font-medium text-warn">需要你决定（{needs.length}）</div>
          <div className="space-y-2">
            {needs.map((s) => (
              <DecisionCard key={s.id} slot={s} ep={ep} proto={proto} />
            ))}
          </div>
        </div>
      )}
      {optionalMissing.length > 0 && (
        <div>
          <div className="mb-2 text-[12.5px] font-medium text-text-2">没找到，可以跳过（{optionalMissing.length}）</div>
          <div className="space-y-2">
            {optionalMissing.map((s) => (
              <DecisionCard key={s.id} slot={s} ep={ep} proto={proto} />
            ))}
          </div>
        </div>
      )}
      <div>
        <div className="mb-2 text-[12.5px] font-medium text-text-2">已识别（{auto.length}）</div>
        <div className="divide-y divide-hairline-soft rounded-[8px] border border-hairline-soft">
          {auto.map((s) => (
            <AutoRow key={s.id} slot={s} ep={ep} proto={proto} />
          ))}
        </div>
      </div>
      {unsupported.length > 0 && (
        <div className="text-[12px] text-text-4">
          标为不支持：{unsupported.map((s) => s.id).join("、")}。项目里对应的控件会禁用。
        </div>
      )}
    </div>
  );
}

function DecisionCard({ slot, ep, proto }: { slot: SlotMeta; ep: WorkflowEndpoint; proto: ComfyProto }) {
  const b = ep.bindings[slot.id];
  return (
    <div className={`rounded-[9px] border p-3.5 ${b.status === "ambiguous" || slot.required ? "border-warn/40 bg-warn/5" : "border-hairline-soft bg-bg-grad-a/30"}`}>
      <div className="flex items-center gap-3">
        <SlotTag id={slot.id} />
        <StatusPill status={b.status} required={slot.required} />
        <span className="ml-auto">
          <SlotActions slot={slot.id} binding={b} proto={proto} />
        </span>
      </div>
      <p className="mt-1 text-[12px] text-text-3">{b.status === "ambiguous" ? `有 ${b.candidates.length} 个位置得分一样，ArcReel 不替你猜。选一个：` : `没在 workflow 里找到像「${slot.label}」的字段。${slot.required ? "这是必填槽位，得手动指一个。" : "可以手选，也可以标为不支持。"}`}</p>
      <div className="mt-2 space-y-2">
        {b.candidates.length > 0 && <CandidateList slot={slot.id} binding={b} proto={proto} />}
        <div className="max-w-md">
          <ManualPicker slot={slot.id} ep={ep} proto={proto} compact />
        </div>
      </div>
    </div>
  );
}

function AutoRow({ slot, ep, proto }: { slot: SlotMeta; ep: WorkflowEndpoint; proto: ComfyProto }) {
  const [open, setOpen] = useState(false);
  const b = ep.bindings[slot.id];
  return (
    <div className="px-3 py-2">
      <div className="flex items-center gap-3">
        <div className="w-44">
          <SlotTag id={slot.id} size="sm" />
        </div>
        <StatusPill status={b.status} />
        <span className="min-w-0 flex-1 truncate text-[12px]">{b.chosen && <TargetLabel c={b.chosen} />}</span>
        <button type="button" className="text-[11.5px] text-text-3 hover:text-text" onClick={() => setOpen((o) => !o)}>
          {open ? "收起" : "改"}
        </button>
      </div>
      {open && (
        <div className="ml-44 mt-2 space-y-2">
          <p className="text-[11.5px] text-text-4">识别依据：{b.chosen?.signals.join(" · ")}</p>
          <CandidateList slot={slot.id} binding={b} proto={proto} verbose={false} />
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
}

function StepModel({ proto }: { proto: ComfyProto }) {
  return (
    <div>
      <StepHeader kicker="Step 4" title="给它起个在项目里的名字" desc="模型行是项目里选到的那个名字。它挂在这台服务上、指向刚保存的 workflow 端点。费用固定 0；单价字段只给企业内部分摊用。" />
      <ModelRowsEditor proto={proto} />
    </div>
  );
}

function StepTrial({ proto, ep }: { proto: ComfyProto; ep: WorkflowEndpoint }) {
  return (
    <div className="space-y-5">
      <StepHeader kicker="Step 5" title="试跑一次" desc="先看 ArcReel 会往 workflow 里写什么，再真的提交一次。这一步可以跳过。" />
      <div>
        <div className="mb-1.5 text-[12.5px] font-medium text-text">预览请求</div>
        <PreviewRequestBlock ep={ep} proto={proto} />
      </div>
      <div>
        <div className="mb-1.5 text-[12.5px] font-medium text-text">真实提交</div>
        <TrialRunBlock proto={proto} />
      </div>
      <div className="rounded-[8px] border border-hairline-soft bg-bg-grad-a/30 p-3 text-[12px] text-text-3">
        <div className={KICKER_CLS}>Summary</div>
        <ul className="mt-1 space-y-0.5">
          <li>
            服务 <span className="text-text">{proto.provider.name}</span> <span className={MONO_CLS}>{proto.provider.baseUrl}</span>
          </li>
          <li>
            workflow 端点 <span className="text-text">{ep.name}</span> <span className={MONO_CLS}>{ep.key}</span> · {ep.mediaType}
          </li>
          <li>
            模型行 {proto.models.map((m) => m.name).join("、") || "—"}
          </li>
        </ul>
      </div>
    </div>
  );
}
